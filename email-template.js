
const os = require("os");
const fs = require("fs");
const https = require("https");
const http = require("http");
const { execFile } = require("child_process");
const { promisify } = require("util");
const express = require("express");

const exec = promisify(execFile);

// --------------------------------------------------
// CONFIGURATION
// --------------------------------------------------

function loadEnv() {
    try {
        const content = fs.readFileSync(".env", "utf8");

        content.split(/\r?\n/).forEach(line => {
            line = line.trim();

            if (!line || line.startsWith("#")) return;

            const index = line.indexOf("=");

            if (index === -1) return;

            const key = line.slice(0, index).trim();
            const value = line.slice(index + 1).trim();

            if (!process.env[key]) {
                process.env[key] = value;
            }
        });
    } catch {
        console.error("Could not load .env");
    }
}

loadEnv();

const CONFIG = {
    webhook: process.env.DISCORD_WEBHOOK_URL,
    hostname: process.env.HOST_NAME || os.hostname(),

    monitorInterval: Number(process.env.MONITOR_INTERVAL || 60000),
    reportInterval: Number(process.env.REPORT_INTERVAL || 1800000),
    securityInterval: Number(process.env.SECURITY_INTERVAL || 300000),
    sslInterval: Number(process.env.SSL_INTERVAL || 43200000),

    cpuThreshold: Number(process.env.CPU_THRESHOLD || 85),
    ramThreshold: Number(process.env.RAM_THRESHOLD || 85),
    diskThreshold: Number(process.env.DISK_THRESHOLD || 85),
    swapThreshold: Number(process.env.SWAP_THRESHOLD || 80),

    urls: (process.env.CHECK_URLS || "")
        .split(",").map(x => x.trim()).filter(Boolean),

    sslDomains: (process.env.SSL_DOMAINS || "")
        .split(",").map(x => x.trim()).filter(Boolean),

    services: (process.env.SYSTEM_SERVICES || "")
        .split(",").map(x => x.trim()).filter(Boolean),

    pm2Apps: (process.env.PM2_APPS || "")
        .split(",").map(x => x.trim()).filter(Boolean),

    security: process.env.ENABLE_SECURITY_MONITOR !== "false"
};

if (!CONFIG.webhook) {
    console.error("DISCORD_WEBHOOK_URL is missing!");
    process.exit(1);
}

// --------------------------------------------------
// STATE
// --------------------------------------------------

let previousCPU = null;
let previousNetwork = null;
let previousSecurityCheck = Date.now() - 300000;

const alertStates = new Map();
const lastSecurityEvents = new Set();

let lastReport = 0;
let lastSecurity = 0;
let lastSSL = 0;

const startedAt = Date.now();

// --------------------------------------------------
// HELPERS
// --------------------------------------------------

function round(value, digits = 2) {
    return Number(Number(value || 0).toFixed(digits));
}

function formatBytes(bytes) {
    const units = ["B", "KB", "MB", "GB", "TB"];

    let i = 0;

    while (bytes >= 1024 && i < units.length - 1) {
        bytes /= 1024;
        i++;
    }

    return `${round(bytes, 2)} ${units[i]}`;
}

function duration(seconds) {
    const d = Math.floor(seconds / 86400);
    const h = Math.floor(seconds % 86400 / 3600);
    const m = Math.floor(seconds % 3600 / 60);

    return `${d}d ${h}h ${m}m`;
}

async function command(name, args = [], timeout = 10000) {
    try {
        const result = await exec(name, args, {
            timeout,
            maxBuffer: 1024 * 1024
        });

        return result.stdout.trim();
    } catch (error) {
        return null;
    }
}

function readFile(path) {
    try {
        return fs.readFileSync(path, "utf8");
    } catch {
        return "";
    }
}

// --------------------------------------------------
// DISCORD
// --------------------------------------------------

async function discord(title, description, color = 3447003) {
    const body = JSON.stringify({
        username: "VPS Monitoring",
        embeds: [{
            title,
            description,
            color,
            timestamp: new Date().toISOString(),
            footer: {
                text: CONFIG.hostname
            }
        }]
    });

    try {
        const response = await fetch(CONFIG.webhook, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body
        });

        if (!response.ok) {
            console.error("Discord error:", response.status);
        }
    } catch (error) {
        console.error("Discord delivery failed:", error.message);
    }
}

async function alert(key, title, message, critical = false) {
    const state = alertStates.get(key);

    // Do not repeat an already active alert.
    if (state === "active") return;

    alertStates.set(key, "active");

    await discord(
        `${critical ? "🚨 CRITICAL" : "⚠️ WARNING"}: ${title}`,
        message,
        critical ? 15158332 : 16753920
    );
}

async function recover(key, title, message) {
    if (alertStates.get(key) !== "active") return;

    alertStates.set(key, "resolved");

    await discord(
        `✅ RECOVERED: ${title}`,
        message,
        3066993
    );
}

// --------------------------------------------------
// CPU
// --------------------------------------------------

function cpuSnapshot() {
    const cpus = os.cpus();

    let idle = 0;
    let total = 0;

    cpus.forEach(cpu => {
        const t = cpu.times;

        idle += t.idle;

        total += t.user + t.nice +
            t.sys + t.idle + t.irq;
    });

    return { idle, total, cores: cpus.length };
}

function getCPUUsage() {
    const current = cpuSnapshot();

    if (!previousCPU) {
        previousCPU = current;
        return 0;
    }

    const idle = current.idle - previousCPU.idle;
    const total = current.total - previousCPU.total;

    previousCPU = current;

    return total > 0
        ? round((1 - idle / total) * 100)
        : 0;
}

// --------------------------------------------------
// MEMORY
// --------------------------------------------------

function getMemory() {
    const mem = readFile("/proc/meminfo");

    const get = key => {
        const match = mem.match(
            new RegExp(`^${key}:\\s+(\\d+)`, "m")
        );

        return match ? Number(match[1]) * 1024 : 0;
    };

    const total = get("MemTotal");
    const available = get("MemAvailable");
    const used = total - available;

    const swapTotal = get("SwapTotal");
    const swapFree = get("SwapFree");

    return {
        total,
        used,
        available,
        percentage: round(used / total * 100),

        swapTotal,
        swapUsed: swapTotal - swapFree,
        swapPercentage: swapTotal
            ? round((swapTotal - swapFree) / swapTotal * 100)
            : 0
    };
}

// --------------------------------------------------
// DISK
// --------------------------------------------------

async function getDisk() {
    const output = await command("df", [
        "-P", "-B1", "-x", "tmpfs",
        "-x", "devtmpfs", "-x", "overlay"
    ]);

    if (!output) return [];

    return output.split("\n").slice(1)
        .filter(Boolean)
        .map(line => {
            const p = line.trim().split(/\s+/);

            if (p.length < 6) return null;

            return {
                filesystem: p[0],
                total: Number(p[1]),
                used: Number(p[2]),
                available: Number(p[3]),
                percentage: Number(p[4].replace("%", "")),
                mount: p[5]
            };
        })
        .filter(Boolean);
}

async function getInodes() {
    const output = await command("df", ["-Pi"]);

    if (!output) return [];

    return output.split("\n").slice(1)
        .filter(Boolean)
        .map(line => {
            const p = line.trim().split(/\s+/);

            if (p.length < 6) return null;

            return {
                mount: p[5],
                percentage: Number(p[4].replace("%", ""))
            };
        })
        .filter(Boolean);
}

// --------------------------------------------------
// NETWORK
// --------------------------------------------------

function getNetwork() {
    const data = readFile("/proc/net/dev");

    const result = {};

    data.split("\n").slice(2).forEach(line => {
        const parts = line.trim().split(/\s+/);

        if (parts.length < 10) return;

        const name = parts[0].replace(":", "");

        if (name === "lo") return;

        result[name] = {
            received: Number(parts[1]),
            rxPackets: Number(parts[2]),
            rxErrors: Number(parts[3]),
            rxDropped: Number(parts[4]),

            transmitted: Number(parts[9]),
            txPackets: Number(parts[10]),
            txErrors: Number(parts[11]),
            txDropped: Number(parts[12])
        };
    });

    return result;
}

function networkStats() {
    const current = getNetwork();

    let rx = 0;
    let tx = 0;

    Object.values(current).forEach(v => {
        rx += v.received;
        tx += v.transmitted;
    });

    const now = Date.now();

    let result = {
        received: formatBytes(rx),
        transmitted: formatBytes(tx),
        downloadSpeed: "Calculating",
        uploadSpeed: "Calculating",
        interfaces: current
    };

    if (previousNetwork) {
        const seconds = (now - previousNetwork.time) / 1000;

        if (seconds > 0) {
            result.downloadSpeed = formatBytes(
                Math.max(0, rx - previousNetwork.rx) / seconds
            ) + "/s";

            result.uploadSpeed = formatBytes(
                Math.max(0, tx - previousNetwork.tx) / seconds
            ) + "/s";
        }
    }

    previousNetwork = { rx, tx, time: now };

    return result;
}

// --------------------------------------------------
// PROCESSES
// --------------------------------------------------

async function getTopProcesses() {
    const output = await command("ps", [
        "-eo", "pid,comm,%cpu,%mem",
        "--sort=-%cpu"
    ]);

    if (!output) return [];

    return output.split("\n").slice(1, 7);
}

// --------------------------------------------------
// SERVICES
// --------------------------------------------------

async function getServices() {
    const results = [];

    for (const service of CONFIG.services) {
        const status = await command("systemctl", [
            "is-active", service
        ]);

        results.push({
            name: service,
            status: status || "unknown"
        });

        if (status !== "active") {
            await alert(
                `service-${service}`,
                `${service} service stopped`,
                `System service: ${service}\nStatus: ${status || "unknown"}`,
                true
            );
        } else {
            await recover(
                `service-${service}`,
                `${service} service`,
                `${service} is running again.`
            );
        }
    }

    return results;
}

// --------------------------------------------------
// PM2
// --------------------------------------------------

async function getPM2() {
    const output = await command("pm2", [
        "jlist"
    ]);

    if (!output) return [];

    try {
        const processes = JSON.parse(output);

        const results = processes.map(p => ({
            name: p.name,
            status: p.pm2_env?.status || "unknown",
            restarts: p.pm2_env?.restart_time || 0,
            memory: formatBytes(p.monit?.memory || 0),
            cpu: p.monit?.cpu || 0
        }));

        for (const app of CONFIG.pm2Apps) {
            const found = results.find(p => p.name === app);

            if (!found || found.status !== "online") {
                await alert(
                    `pm2-${app}`,
                    `PM2 application failure: ${app}`,
                    `Application: ${app}\nStatus: ${found?.status || "not found"}`,
                    true
                );
            } else {
                await recover(
                    `pm2-${app}`,
                    `PM2 ${app}`,
                    `${app} is online again.`
                );
            }
        }

        return results;
    } catch {
        return [];
    }
}

// --------------------------------------------------
// WEBSITE CHECKS
// --------------------------------------------------

function checkURL(url) {
    return new Promise(resolve => {
        const start = Date.now();

        let finished = false;

        const done = result => {
            if (finished) return;
            finished = true;
            resolve(result);
        };

        try {
            const client = url.startsWith("https")
                ? https : http;

            const req = client.get(url, {
                timeout: 10000
            }, res => {
                res.resume();

                done({
                    url,
                    status: res.statusCode,
                    online: res.statusCode >= 200 &&
                            res.statusCode < 400,
                    latency: Date.now() - start
                });
            });

            req.on("timeout", () => {
                req.destroy();
                done({
                    url,
                    online: false,
                    error: "Timeout"
                });
            });

            req.on("error", error => {
                done({
                    url,
                    online: false,
                    error: error.message
                });
            });
        } catch (error) {
            done({
                url,
                online: false,
                error: error.message
            });
        }
    });
}

async function getWebsites() {
    const results = [];

    for (const url of CONFIG.urls) {
        const result = await checkURL(url);

        results.push(result);

        const key = `website-${url}`;

        if (!result.online) {
            await alert(
                key,
                "Website unavailable",
                `URL: ${url}\nError: ${result.error || result.status}`,
                true
            );
        } else {
            await recover(
                key,
                "Website recovered",
                `${url} responded with HTTP ${result.status}.`
            );
        }
    }

    return results;
}

// --------------------------------------------------
// SSL CERTIFICATES
// --------------------------------------------------

async function checkSSL(domain) {
    const output = await command("openssl", [
        "s_client",
        "-connect", `${domain}:443`,
        "-servername", domain,
        "-verify_return_error"
    ], 12000);

    // openssl s_client output can be unavailable depending
    // on certificate and verification errors. Use x509
    // with a separate connection for expiry information.
    const expiry = await command("bash", [
        "-c",
        `timeout 10 openssl s_client -connect '${domain}:443' -servername '${domain}' </dev/null 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null`
    ], 15000);

    if (!expiry) {
        return {
            domain,
            valid: false,
            error: "Certificate unavailable"
        };
    }

    const match = expiry.match(/notAfter=(.*)/);

    if (!match) {
        return { domain, valid: false };
    }

    const expiryDate = new Date(match[1]);
    const days = Math.floor(
        (expiryDate.getTime() - Date.now()) / 86400000
    );

    return {
        domain,
        valid: days >= 0,
        daysRemaining: days,
        expiry: expiryDate.toISOString()
    };
}

async function checkAllSSL() {
    for (const domain of CONFIG.sslDomains) {
        const cert = await checkSSL(domain);

        if (!cert.valid || cert.daysRemaining <= 14) {
            await alert(
                `ssl-${domain}`,
                `SSL certificate warning: ${domain}`,
                `Domain: ${domain}\nRemaining days: ${cert.daysRemaining ?? "Unknown"}\nExpiry: ${cert.expiry || cert.error}`,
                true
            );
        } else {
            await recover(
                `ssl-${domain}`,
                `SSL certificate ${domain}`,
                `Certificate is valid for another ${cert.daysRemaining} days.`
            );
        }
    }
}

// --------------------------------------------------
// SECURITY
// --------------------------------------------------

async function getSecurity() {
    if (!CONFIG.security) return;

    const now = Date.now();

    const since = Math.floor(previousSecurityCheck / 1000);

    previousSecurityCheck = now;

    const output = await command("journalctl", [
        "--since", `@${since}`,
        "--no-pager",
        "-o", "short-iso",
        "-u", "ssh",
        "-u", "sshd"
    ]);

    if (!output) return;

    const lines = output.split("\n");

    const events = lines.filter(line =>
        /Failed password|Accepted password|Accepted publickey|Invalid user/i.test(line)
    );

    for (const event of events.slice(-30)) {
        const fingerprint = event.trim();

        if (lastSecurityEvents.has(fingerprint)) continue;

        lastSecurityEvents.add(fingerprint);

        const failed = /Failed password|Invalid user/i.test(event);

        await discord(
            failed
                ? "🔐 Failed SSH login"
                : "🔑 Successful SSH login",
            `Server: ${CONFIG.hostname}\n\`\`\`\n${event.slice(0, 900)}\n\`\`\``,
            failed ? 15158332 : 3066993
        );
    }

    if (lastSecurityEvents.size > 500) {
        lastSecurityEvents.clear();
    }
}

async function getFirewall() {
    const ufw = await command("ufw", ["status"]);

    const failed = await command("systemctl", [
        "is-failed"
    ]);

    const users = await command("who", []);

    const ports = await command("ss", [
        "-lntup"
    ]);

    return {
        firewall: ufw || "Unavailable",
        failedServices: failed || "Unavailable",
        loggedInUsers: users || "None",
        listeningPorts: ports || "Unavailable"
    };
}

// --------------------------------------------------
// SYSTEM INFORMATION
// --------------------------------------------------

async function getSystem() {
    const disk = await getDisk();
    const memory = getMemory();
    const cpu = getCPUUsage();
    const network = networkStats();

    const info = {
        hostname: CONFIG.hostname,
        platform: os.platform(),
        architecture: os.arch(),
        kernel: os.release(),
        nodeVersion: process.version,
        cores: os.cpus().length,
        uptime: duration(os.uptime()),
        cpu,
        load: os.loadavg().map(x => round(x)),
        memory,
        disk,
        inodes: await getInodes(),
        network,
        topProcesses: await getTopProcesses()
    };

    // Threshold alerts

    if (cpu >= CONFIG.cpuThreshold) {
        await alert("cpu", "High CPU usage",
            `CPU usage: ${cpu}%`);
    } else {
        await recover("cpu", "CPU usage", `CPU is now ${cpu}%.`);
    }

    if (memory.percentage >= CONFIG.ramThreshold) {
        await alert("ram", "High RAM usage",
            `RAM usage: ${memory.percentage}%`);
    } else {
        await recover("ram", "RAM usage",
            `RAM is now ${memory.percentage}%.`);
    }

    if (memory.swapPercentage >= CONFIG.swapThreshold) {
        await alert("swap", "High swap usage",
            `Swap usage: ${memory.swapPercentage}%`);
    } else {
        await recover("swap", "Swap usage",
            `Swap is now ${memory.swapPercentage}%.`);
    }

    for (const d of disk) {
        if (d.percentage >= CONFIG.diskThreshold) {
            await alert(
                `disk-${d.mount}`,
                "Disk space warning",
                `${d.mount}: ${d.percentage}% used`,
                true
            );
        } else {
            await recover(
                `disk-${d.mount}`,
                "Disk space",
                `${d.mount} is now ${d.percentage}% used.`
            );
        }
    }

    return info;
}

// --------------------------------------------------
// FULL REPORT
// --------------------------------------------------

async function sendFullReport() {
    const system = await getSystem();

    const services = await getServices();
    const pm2 = await getPM2();
    const websites = await getWebsites();
    const firewall = await getFirewall();

    const diskText = system.disk.map(d =>
        `${d.mount}: ${d.percentage}% (${formatBytes(d.available)} free)`
    ).join("\n");

    const memory = system.memory;

    const processText = system.topProcesses.join("\n");

    const serviceText = services.map(s =>
        `${s.name}: ${s.status}`
    ).join("\n");

    const pm2Text = pm2.map(p =>
        `${p.name}: ${p.status} | CPU ${p.cpu}% | RAM ${p.memory}`
    ).join("\n") || "No PM2 information";

    const websiteText = websites.map(w =>
        `${w.online ? "🟢" : "🔴"} ${w.url} | ${w.status || w.error}`
    ).join("\n") || "No websites configured";

    const network = system.network;

    const message = [
        `**🖥️ SERVER:** ${system.hostname}`,
        `**OS:** ${system.platform} ${system.architecture}`,
        `**Kernel:** ${system.kernel}`,
        `**Node:** ${system.nodeVersion}`,
        `**CPU Cores:** ${system.cores}`,
        `**Uptime:** ${system.uptime}`,
        "",
        "**📊 CPU**",
        `Usage: ${system.cpu}%`,
        `Load: ${system.load.join(" / ")}`,
        "",
        "**🧠 MEMORY**",
        `Used: ${formatBytes(memory.used)} / ${formatBytes(memory.total)}`,
        `Usage: ${memory.percentage}%`,
        `Swap: ${memory.swapPercentage}%`,
        "",
        "**💾 DISK**",
        diskText || "Unavailable",
        "",
        "**🌐 NETWORK**",
        `Download: ${network.downloadSpeed}`,
        `Upload: ${network.uploadSpeed}`,
        `Received: ${network.received}`,
        `Transmitted: ${network.transmitted}`,
        "",
        "**⚙️ SYSTEM SERVICES**",
        serviceText || "No services configured",
        "",
        "**🚀 PM2 APPLICATIONS**",
        pm2Text,
        "",
        "**🌍 WEBSITES**",
        websiteText,
        "",
        "**🔥 FIREWALL**",
        (firewall.firewall || "").slice(0, 500),
        "",
        "**👥 LOGGED IN USERS**",
        (firewall.loggedInUsers || "Unavailable").slice(0, 400),
        "",
        "**🔌 LISTENING PORTS**",
        (firewall.listeningPorts || "Unavailable").slice(0, 700),
        "",
        "**📈 TOP PROCESSES**",
        `\`\`\`\n${processText.slice(0, 500)}\n\`\`\``
    ].join("\n");

    // Discord description limit is 4096 characters.
    await discord(
        "📋 Complete VPS Health Report",
        message.slice(0, 4000),
        3447003
    );
}

// --------------------------------------------------
// BASIC HEALTH ENDPOINT
// --------------------------------------------------

const app = express();

app.get("/health", (req, res) => {
    res.json({
        status: "running",
        hostname: CONFIG.hostname,
        uptime: duration((Date.now() - startedAt) / 1000),
        timestamp: new Date().toISOString()
    });
});

app.listen(5055, "127.0.0.1", () => {
    console.log("Monitoring health endpoint: 127.0.0.1:5055");
});

// --------------------------------------------------
// MONITORING LOOP
// --------------------------------------------------

let running = false;

async function monitor() {
    if (running) return;

    running = true;

    try {
        const now = Date.now();

        // Frequent metrics and threshold alerts
        await getSystem();

        // Application health checks
        await getServices();
        await getPM2();
        await getWebsites();

        // Security checks
        if (now - lastSecurity >= CONFIG.securityInterval) {
            lastSecurity = now;
            await getSecurity();
        }

        // SSL checks
        if (now - lastSSL >= CONFIG.sslInterval) {
            lastSSL = now;
            await checkAllSSL();
        }

        // Full report
        if (now - lastReport >= CONFIG.reportInterval) {
            lastReport = now;
            await sendFullReport();
        }

    } catch (error) {
        console.error("Monitoring error:", error);
    } finally {
        running = false;
    }
}

// --------------------------------------------------
// START
// --------------------------------------------------

async function start() {
    console.log("Starting VPS monitoring...");

    await discord(
        "🟢 VPS Monitoring Started",
        `Server: ${CONFIG.hostname}\nOS: ${os.platform()}\nNode: ${process.version}\nMonitoring is now active.`,
        3066993
    );

    await checkAllSSL();
    await monitor();

    setInterval(monitor, CONFIG.monitorInterval);

    console.log("VPS monitoring is active.");
}

process.on("uncaughtException", error => {
    console.error(error);
    discord("🚨 Monitor process error", error.message, 15158332);
});

process.on("unhandledRejection", error => {
    console.error(error);
    discord("🚨 Unhandled promise rejection", String(error), 15158332);
});

start();
