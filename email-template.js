function getEmailTemplate(user) {
    const name = user.name || user.fullName || "User";

    return {
        subject: "🚀 A Better UniKart Is Here! We've Upgraded Your Experience.",

        html: `
       
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>A Better UniKart Is Here!</title>
</head>

<body style="margin:0;padding:0;background:#f3f6fb;font-family:Inter,Segoe UI,Arial,sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#f3f6fb;padding:40px 15px;">

<tr>
<td align="center">

<!-- Main Container -->
<table width="620" cellpadding="0" cellspacing="0" border="0"
       style="max-width:620px;width:100%;background:#ffffff;border-radius:24px;overflow:hidden;box-shadow:0 15px 40px rgba(0,0,0,0.08);">

    <!-- Header -->
    <tr>
        <td align="center"
            style="padding:45px 30px;background:#2563eb;background:linear-gradient(135deg,#2563EB,#4F46E5);">

            <div style="width:85px;height:85px;background:rgba(255,255,255,0.16);border-radius:50%;font-size:43px;line-height:85px;text-align:center;">
                🚀
            </div>

            <h1 style="margin:22px 0 10px;font-size:34px;font-weight:700;color:#ffffff;">
                A Better UniKart Is Here!
            </h1>

            <p style="margin:0;font-size:16px;line-height:25px;color:#dbeafe;">
                Bigger Improvements. Better Experience. We're Back!
            </p>

        </td>
    </tr>

    <!-- Body -->
    <tr>
        <td style="padding:40px 40px 30px;">

            <h2 style="margin:0;font-size:27px;color:#111827;font-weight:700;">
                Hello ${customer_name} 👋
            </h2>

            <p style="font-size:16px;line-height:28px;color:#6b7280;margin-top:20px;">
                We've got something exciting to share with you!
            </p>

            <p style="font-size:16px;line-height:28px;color:#4b5563;margin-top:12px;">
                After a brief pause, <strong style="color:#2563eb;">UniKart is officially back in action!</strong>
                And this time, we're returning with more than just a comeback.
                We've been working behind the scenes to bring you an improved
                platform, enhanced performance and a better overall experience.
            </p>

            <p style="font-size:16px;line-height:28px;color:#4b5563;">
                The recent period has been an important step forward for UniKart.
                We've strengthened our systems, introduced key improvements
                and upgraded our platform infrastructure to make your
                experience smoother, faster and more reliable.
            </p>

            <p style="font-size:16px;line-height:28px;color:#4b5563;">
                <strong style="color:#111827;">
                    And now, the wait is over!
                </strong>
                Starting from the moment you receive this email, all our services
                are back online and ready for you to explore.
            </p>

            <!-- System Upgrade Card -->
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="margin:30px 0;background:#eff6ff;border-radius:16px;">

                <tr>
                    <td style="padding:25px;">

                        <table width="100%" cellpadding="0" cellspacing="0">
                            <tr>

                                <td width="55" valign="top">
                                    <div style="width:45px;height:45px;border-radius:50%;background:#2563eb;color:#ffffff;font-size:24px;text-align:center;line-height:45px;">
                                        ⚡
                                    </div>
                                </td>

                                <td>
                                    <h3 style="margin:0;font-size:19px;color:#1e40af;">
                                        System Successfully Upgraded!
                                    </h3>

                                    <p style="margin:8px 0 0;font-size:14px;color:#1d4ed8;line-height:23px;">
                                        Improved infrastructure, enhanced performance
                                        and a more reliable UniKart experience.
                                    </p>
                                </td>

                            </tr>
                        </table>

                    </td>
                </tr>
            </table>

            <!-- Improvements -->
            <h3 style="margin:30px 0 18px;text-align:center;font-size:20px;color:#111827;">
                What's Better with UniKart?
            </h3>

            <table width="100%" cellpadding="7" cellspacing="0" border="0">

                <tr>
                    <td width="50%" valign="top"
                        style="background:#f8fafc;border-radius:14px;padding:20px 12px;">

                        <div style="font-size:27px;text-align:center;">⚡</div>

                        <h4 style="margin:10px 0 5px;text-align:center;color:#1f2937;font-size:15px;">
                            Improved Performance
                        </h4>

                        <p style="margin:0;text-align:center;font-size:12px;color:#6b7280;line-height:20px;">
                            A smoother and more responsive platform experience.
                        </p>

                    </td>

                    <td width="50%" valign="top"
                        style="background:#f8fafc;border-radius:14px;padding:20px 12px;">

                        <div style="font-size:27px;text-align:center;">🛡️</div>

                        <h4 style="margin:10px 0 5px;text-align:center;color:#1f2937;font-size:15px;">
                            Enhanced Reliability
                        </h4>

                        <p style="margin:0;text-align:center;font-size:12px;color:#6b7280;line-height:20px;">
                            Strengthened systems for dependable services.
                        </p>

                    </td>
                </tr>

                <tr>
                    <td width="50%" valign="top"
                        style="background:#f8fafc;border-radius:14px;padding:20px 12px;">

                        <div style="font-size:27px;text-align:center;">🔧</div>

                        <h4 style="margin:10px 0 5px;text-align:center;color:#1f2937;font-size:15px;">
                            System Upgrades
                        </h4>

                        <p style="margin:0;text-align:center;font-size:12px;color:#6b7280;line-height:20px;">
                            Improved platform infrastructure and technology.
                        </p>

                    </td>

                    <td width="50%" valign="top"
                        style="background:#f8fafc;border-radius:14px;padding:20px 12px;">

                        <div style="font-size:27px;text-align:center;">✨</div>

                        <h4 style="margin:10px 0 5px;text-align:center;color:#1f2937;font-size:15px;">
                            Better Experience
                        </h4>

                        <p style="margin:0;text-align:center;font-size:12px;color:#6b7280;line-height:20px;">
                            A more convenient way to shop, print and manage orders.
                        </p>

                    </td>
                </tr>

            </table>

            <!-- Services -->
            <h3 style="margin:35px 0 15px;text-align:center;font-size:19px;color:#111827;">
                Your Favourite Services Are Back!
            </h3>

            <table width="100%" cellpadding="6" cellspacing="0" border="0">
                <tr>

                    <td width="33%" align="center"
                        style="background:#f8fafc;border-radius:14px;padding:18px 5px;">

                        <div style="font-size:27px;">🛒</div>

                        <p style="margin:9px 0 0;font-size:13px;font-weight:600;color:#374151;">
                            Shopping
                        </p>
                    </td>

                    <td width="33%" align="center"
                        style="background:#f8fafc;border-radius:14px;padding:18px 5px;">

                        <div style="font-size:27px;">🖨️</div>

                        <p style="margin:9px 0 0;font-size:13px;font-weight:600;color:#374151;">
                            Printing
                        </p>
                    </td>

                    <td width="33%" align="center"
                        style="background:#f8fafc;border-radius:14px;padding:18px 5px;">

                        <div style="font-size:27px;">📦</div>

                        <p style="margin:9px 0 0;font-size:13px;font-weight:600;color:#374151;">
                            Orders
                        </p>
                    </td>

                </tr>
            </table>

            <!-- Thank You Card -->
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="margin:30px 0;background:#ecfdf5;border-radius:16px;">

                <tr>
                    <td style="padding:23px;">

                        <h3 style="margin:0 0 10px;font-size:18px;color:#065f46;">
                            Thank You for Staying with Us! 💚
                        </h3>

                        <p style="margin:0;font-size:14px;line-height:24px;color:#047857;">
                            We sincerely apologize for the inconvenience
                            during the recent service interruption.
                            Your patience, understanding and continued trust
                            have motivated us to come back stronger.
                            We're excited to serve you with an improved UniKart!
                        </p>

                    </td>
                </tr>
            </table>

            <!-- CTA -->
            <div style="text-align:center;margin:40px 0 30px;">

                <p style="font-size:16px;color:#374151;line-height:25px;margin-bottom:22px;">
                    The upgraded UniKart experience is waiting for you.
                    Come and explore what's new!
                </p>

                <a href="https://app.unikart.com/open"
                   style="display:inline-block;background:#2563eb;color:#ffffff;padding:17px 42px;border-radius:12px;font-size:16px;font-weight:600;text-decoration:none;box-shadow:0 10px 25px rgba(37,99,235,0.25);">

                    Experience the New UniKart →
                </a>

            </div>

            <p style="font-size:14px;color:#9ca3af;line-height:24px;text-align:center;">
                Have questions or need assistance?
                Our support team is always here to help.
            </p>

            <p style="font-size:16px;font-weight:600;color:#374151;text-align:center;margin-top:25px;">
                Here's to a better experience!<br>
                Team UniKart ❤️
            </p>

        </td>
    </tr>

    <!-- Footer -->
    <tr>
        <td align="center" style="background:#f9fafb;padding:30px;">

            <h3 style="margin:0;color:#2563eb;font-size:24px;">
                UniKart
            </h3>

            <p style="margin:10px 0 0;font-size:13px;color:#9ca3af;">
                Smart Shopping • Easy Printing • Fast Delivery
            </p>

            <p style="margin:15px 0 0;font-size:12px;color:#9ca3af;line-height:20px;">
                Better Technology. Better Experience. Better UniKart.<br><br>
                © 2026 UniKart. All Rights Reserved.
            </p>

        </td>
    </tr>

</table>

</td>
</tr>
</table>

</body>
</html>

        `
    };
}

function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
    })[char]);
}

module.exports = getEmailTemplate;
