from datetime import datetime

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from bot.handlers import guard as guard_module
from bot.keyboards.reply_markups import (
    cancel_only,
    confirm_clear,
    confirm_dev,
    confirm_mail,
    confirm_revoke,
    device_rows,
    main_menu,
    manage_dash,
    otp_menu,
)
from bot.services import session_service
from bot.utils.helpers import fmt_device, fmt_phone, is_hex
from config import GUARD_EMAIL, GUARD_EMAIL_APP_PASSWORD


def _verify_msg(info: dict) -> str:
    return (
        "✅ *Account Verified!*\n\n"
        f"📱 Phone      : {fmt_phone(info.get('phone'))}\n"
        f"👤 Name       : {info.get('name') or 'Unknown'}\n"
        f"🆔 User ID    : {info.get('user_id') or 'Unknown'}\n"
        f"📟 Devices    : {info.get('devices')} connected\n"
        f"⚠️ Status     : {info.get('spam') or 'Unknown'}"
    )


def _dash_msg(info: dict) -> str:
    return (
        "🧰 *MANAGE DASHBOARD*\n\n"
        f"📱 Phone : {fmt_phone(info.get('phone'))}\n"
        f"👤 Name  : {info.get('name') or 'Unknown'}\n\n"
        "Choose an option:"
    )


async def _get_client(ctx: ContextTypes.DEFAULT_TYPE, hex_str: str = None):
    client = ctx.user_data.get("client")
    if not client:
        hex_str = hex_str or ctx.user_data.get("hex")
        client = await session_service.make_client(hex_str)
        ctx.user_data["client"] = client
    elif not client.is_connected():
        await client.connect()
    return client


# ------------------------------------------------------------------ entry point
async def ma(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.bot_data["state"].set_wait(update.effective_user.id, "manage")
    await q.edit_message_text(
        "🧰 *Manage Account*\n\n"
        "Send the Telegram session hex string to verify it and open the dashboard.\n\n"
        "Format: `92dc84c8...` (long hex string)",
        reply_markup=cancel_only(),
    )


async def hex_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = ctx.bot_data["state"]
    mode = state.waiting(uid)
    if not mode:
        return
    text = update.message.text.strip()
    if not is_hex(text):
        await update.message.reply_text(
            "⚠️ That doesn't look like a valid session hex.\n\n"
            "Please send the full hex string (a long string of `0-9` and `a-f`).",
            reply_markup=cancel_only(),
        )
        return
    state.clear_wait(uid)
    if mode == "manage":
        await _manage_hex(update, ctx, text)
    elif mode == "guard":
        await guard_module.handle_hex(update, ctx, text)


async def _manage_hex(update: Update, ctx: ContextTypes.DEFAULT_TYPE, hex_str: str):
    uid = update.effective_user.id
    msg = await update.message.reply_text("⏳ Verifying session...")
    try:
        info, client = await session_service.verify(hex_str)
    except Exception as e:
        await msg.edit_text(
            f"❌ *Verification failed*\n\n`{type(e).__name__}: {e}`\n\n"
            "The session may be expired, revoked or invalid.",
            reply_markup=cancel_only(),
        )
        return

    old = ctx.user_data.pop("client", None)
    if old:
        try:
            await old.disconnect()
        except Exception:
            pass
    ctx.user_data["client"] = client
    ctx.user_data["hex"] = hex_str
    ctx.bot_data["state"].set_hex(uid, hex_str)

    ctx.bot_data["accounts"].upsert(hex_str, {
        "phone": info["phone"],
        "name": info["name"],
        "user_id": info["user_id"],
        "spam": info["spam"],
        "devices": info["devices"],
        "active": False,
        "verified_at": datetime.utcnow(),
    })

    await msg.edit_text(_verify_msg(info))
    await update.message.reply_text(_dash_msg(info), reply_markup=manage_dash())


# ------------------------------------------------------------------ dashboard
async def dash(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    hex_str = ctx.bot_data["state"].get_hex(uid)
    if not hex_str:
        await q.edit_message_text(
            "❌ No active session. Start again from *Manage Account*.",
            reply_markup=main_menu(),
        )
        return
    try:
        client = await _get_client(ctx, hex_str)
        info = await session_service.account_summary(client)
    except Exception:
        info = {}
    await q.edit_message_text(_dash_msg(info), reply_markup=manage_dash())


# ------------------------------------------------------------------- devices
async def dev_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        client = await _get_client(ctx)
        devices = await session_service.list_devices(client)
    except Exception as e:
        await q.edit_message_text(
            f"❌ Failed to fetch devices: `{e}`", reply_markup=manage_dash()
        )
        return
    text = (
        f"📱 *DEVICE DASHBOARD*\n\n"
        f"Total sessions: {len(devices)}\n\n"
        "Tap a device to terminate it:"
    )
    await q.edit_message_text(text, reply_markup=device_rows(devices))


async def dev_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    h = int(q.data.split("_")[1])
