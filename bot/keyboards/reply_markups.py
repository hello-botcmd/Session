from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as M

def main_menu():
    return M([
        [B("🧰 Manage Account", callback_data="ma"),
         B("🛡️ Guard Account", callback_data="guard")],
        [B("👤 My Accounts", callback_data="myacc")],
    ])

def cancel_only():
    return M([[B("❌ Cancel", callback_data="cancel")]])

def manage_dash():
    return M([
        [B("📱 Device Dashboard", callback_data="dev_list"),
         B("🧹 Clear All", callback_data="clear_ask")],
        [B("🔑 Fetch OTP", callback_data="otp_get"),
         B("📧 Change Email", callback_data="mail_ask")],
        [B("⬅️ Back", callback_data="cancel")],
    ])

def back_dash():
    return M([[B("⬅️ Back", callback_data="dash")]])

def device_rows(devices, limit=15):
    rows = []
    for a in devices[:limit]:
        label = f"{a.device_model or 'Device'} · {a.platform or '?'}"
        rows.append([B(f"📱 {label}", callback_data=f"dev_{a.hash}")])
    if len(devices) > limit:
        rows.append([B(f"… +{len(devices)-limit} more devices", callback_data="noop")])
    rows.append([B("🔐 Revoke Bot Session", callback_data="revoke_ask"),
                 B("⬅️ Back", callback_data="dash")])
    return M(rows)

def confirm_dev(h):
    return M([
        [B("✅ Yes, terminate", callback_data=f"dev_yes_{h}"),
         B("❌ No", callback_data=f"dev_no_{h}")],
        [B("❌ Cancel", callback_data="dev_list")],
    ])

def confirm_clear():
    return M([
        [B("✅ Yes, wipe everything", callback_data="clear_yes"),
         B("❌ No", callback_data="clear_no")],
        [B("❌ Cancel", callback_data="dash")],
    ])

def otp_menu():
    return M([
        [B("📩 Read Latest OTP", callback_data="otp_get")],
        [B("❌ Cancel", callback_data="dash")],
    ])

def confirm_mail():
    return M([
        [B("✅ Yes, change email", callback_data="mail_yes"),
         B("❌ No", callback_data="mail_no")],
        [B("❌ Cancel", callback_data="dash")],
    ])

def confirm_revoke():
    return M([
        [B("✅ Yes, revoke", callback_data="revoke_yes"),
         B("❌ No", callback_data="revoke_no")],
        [B("❌ Cancel", callback_data="dash")],
    ])

def acc_page(accs, page, total_pages):
    rows = [[B(f"👤 {a['name'] or '?'}  |  {a.get('phone') or '?'}",
               callback_data=f"acc_{a['_id']}")] for a in accs]
    nav = []
    if page > 0:
        nav.append(B("⬅️ Prev", callback_data=f"page_{page-1}"))
    nav.append(B(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(B("Next ➡️", callback_data=f"page_{page+1}"))
    rows.append(nav)
    rows.append([B("⬅️ Back", callback_data="cancel")])
    return M(rows)

def acc_dash():
    return M([
        [B("🔑 Fetch OTP", callback_data="otp_acc"),
         B("🔌 Revoke Bot Connection", callback_data="revoke_acc")],
        [B("🔓 Allow Login (60s)", callback_data="allow_acc")],
        [B("⬅️ Back", callback_data="myacc")],
    ])

def confirm_revoke_acc():
    return M([
        [B("✅ Yes, revoke", callback_data="revoke_acc_yes"),
         B("❌ No", callback_data="revoke_acc_no")],
        [B("❌ Cancel", callback_data="myacc")],
    ])
