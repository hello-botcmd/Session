from telegram import InlineKeyboardButton as B
from telegram import InlineKeyboardMarkup as M

from bot.utils.helpers import device_hash, is_current_device


def main_menu():
    return M([
        [
            B("🧰 Manage Account", callback_data="ma"),
            B("🛡️ Guard Account", callback_data="guard"),
        ],
        [B("👤 My Accounts", callback_data="myacc")],
    ])


def cancel_only():
    return M([[B("❌ Cancel", callback_data="cancel")]])


def manage_dash():
    return M([
        [
            B("📱 Devices", callback_data="dev_list"),
            B("🧹 Clear All", callback_data="clear_ask"),
        ],
        [
            B("🔑 Fetch OTP", callback_data="otp_get"),
            B("📧 Change Email", callback_data="mail_ask"),
        ],
        [B("⬅️ Back", callback_data="cancel")],
    ])


def device_rows(devices, limit=15):
    rows = []
    items = devices or []
    for item in items[:limit]:
        if isinstance(item, dict):
            model = item.get("device") or "Device"
            plat = item.get("platform") or "?"
        else:
            model = getattr(item, "device_model", None) or "Device"
            plat = getattr(item, "platform", None) or "?"
        mark = " ⚡" if is_current_device(item) else ""
        rows.append([
            B(
                f"📱 {model} · {plat}{mark}",
                callback_data=f"dev_{device_hash(item)}",
            )
        ])
    if len(items) > limit:
        rows.append([B(f"… +{len(items) - limit} more devices", callback_data="noop")])
    rows.append([
        B("🔐 Revoke Bot Session", callback_data="revoke_ask"),
        B("⬅️ Back", callback_data="dash"),
    ])
    return M(rows)


def confirm_dev(h):
    return M([
        [
            B("✅ Yes, terminate", callback_data=f"dev_yes_{h}"),
            B("❌ No", callback_data=f"dev_no_{h}"),
        ],
        [B("⬅️ Back", callback_data="dev_list")],
    ])


def confirm_clear():
    return M([
        [
            B("✅ Yes, wipe everything", callback_data="clear_yes"),
            B("❌ No", callback_data="clear_no"),
        ],
        [B("⬅️ Back", callback_data="dash")],
    ])


def otp_menu():
    return M([
        [B("📩 Read Latest OTP", callback_data="otp_read")],
        [B("⬅️ Back", callback_data="dash")],
    ])


def confirm_mail():
    return M([
        [
            B("✅ Yes, change email", callback_data="mail_yes"),
            B("❌ No", callback_data="mail_no"),
        ],
        [B("⬅️ Back", callback_data="dash")],
    ])


def confirm_revoke():
    return M([
        [
            B("✅ Yes, revoke", callback_data="revoke_yes"),
            B("❌ No", callback_data="revoke_no"),
        ],
        [B("⬅️ Back", callback_data="dash")],
    ])


def acc_page(accs, page, total_pages):
    rows = []
    for acc in accs:
        name = acc.get("name") or "?"
        phone = acc.get("phone") or "?"
        rows.append([
            B(f"👤 {name}  |  {phone}", callback_data=f"acc_{acc['_id']}")
        ])
    nav = []
    if page > 0:
        nav.append(B("⬅️ Prev", callback_data=f"page_{page - 1}"))
    nav.append(B(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(B("Next ➡️", callback_data=f"page_{page + 1}"))
    rows.append(nav)
    rows.append([B("⬅️ Back", callback_data="cancel")])
    return M(rows)


def acc_dash():
    return M([
        [
            B("🔑 Fetch OTP", callback_data="otp_acc"),
            B("🔌 Revoke Bot Connection", callback_data="revoke_acc"),
        ],
        [B("🔓 Allow Login (60s)", callback_data="allow_acc")],
        [B("⬅️ Back", callback_data="myacc")],
    ])


def confirm_revoke_acc():
    return M([
        [
            B("✅ Yes, revoke", callback_data="revoke_acc_yes"),
            B("❌ No", callback_data="revoke_acc_no"),
        ],
        [B("⬅️ Back", callback_data="myacc")],
    ])
