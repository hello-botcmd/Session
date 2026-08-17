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
    try:
        client = await _get_client(ctx)
        devices = await session_service.list_devices(client)
        dev = next((a for a in devices if a.hash == h), None)
    except Exception:
        dev = None
    if not dev:
        await q.edit_message_text(
            "❌ Device not found. Refresh the device list.",
            reply_markup=manage_dash(),
        )
        return
    await q.edit_message_text(
        f"⚠️ *Terminate this device?*\n\n{fmt_device(dev)}\n\n"
        "The session will be logged out on that device.",
        reply_markup=confirm_dev(h),
    )


async def dev_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    h = int(q.data.split("_")[2])
    try:
        client = await _get_client(ctx)
        await session_service.terminate_device(client, h)
        devices = await session_service.list_devices(client)
    except Exception as e:
        await q.edit_message_text(
            f"❌ Failed to terminate device: `{e}`", reply_markup=manage_dash()
        )
        return
    await q.edit_message_text(
        f"✅ *Device terminated.*\n\nRemaining devices: {len(devices)}",
        reply_markup=device_rows(devices),
    )


async def dev_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        client = await _get_client(ctx)
        devices = await session_service.list_devices(client)
    except Exception:
        devices = []
    await q.edit_message_text(
        "✅ Cancelled — device kept.\n\nSelect another device:",
        reply_markup=device_rows(devices),
    )


# ------------------------------------------------------------------ revoke bot
async def revoke_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🔐 *Revoke Bot Session*\n\n"
        "This permanently logs out the bot's own session from this account.\n"
        "After that you must re-verify with a fresh hex.",
        reply_markup=confirm_revoke(),
    )


async def revoke_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    client = ctx.user_data.pop("client", None)
    hex_str = ctx.user_data.pop("hex", None)
    ctx.bot_data["state"].clear_hex(uid)
    if hex_str:
        ctx.bot_data["accounts"].delete(hex_str)
    if client:
        try:
            await session_service.revoke_session(client)
        except Exception:
            try:
                await client.disconnect()
            except Exception:
                pass
    await q.edit_message_text(
        "✅ *Bot session revoked.*\n\n"
        "The connection is logged out and removed from storage.",
        reply_markup=main_menu(),
    )


async def revoke_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "✅ Cancelled — session kept.", reply_markup=manage_dash()
    )


# ------------------------------------------------------------------ clear all
async def clear_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🧹 *Clear All*\n\n"
        "This will wipe EVERYTHING on the account:\n"
        "• All contacts\n"
        "• All private chats & DMs (revoked)\n"
        "• All groups & channels (deleted/left)\n\n"
        "⚠️ This is **irreversible**. Continue?",
        reply_markup=confirm_clear(),
    )


async def clear_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        client = await _get_client(ctx)
    except Exception as e:
        await q.edit_message_text(
            f"❌ Connection failed: `{e}`", reply_markup=manage_dash()
        )
        return

    progress = await q.edit_message_text("🧹 Clearing contacts...")

    async def update_stage(stage):
        nonlocal progress
        label = {
            "contacts": "🧹 Clearing contacts...",
            "dialogs": "💬 Clearing chats...",
        }.get(stage, "⏳ Working...")
        progress = await progress.edit_text(label)

    try:
        stats = await session_service.clear_all(client, update_stage)
    except Exception as e:
        await progress.edit_text(
            f"❌ Clear failed: `{e}`", reply_markup=manage_dash()
        )
        return

    await progress.edit_text(
        "✅ *Account cleared!*\n\n"
        f"👤 Contacts deleted : {stats['contacts']}\n"
        f"💬 Chats deleted    : {stats['dialogs']}\n"
        f"👥 Groups left      : {stats['groups']}\n"
        f"📢 Channels deleted : {stats['channels']}\n\n"
        "All done.",
        reply_markup=manage_dash(),
    )


async def clear_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "✅ Cancelled — nothing was touched.", reply_markup=manage_dash()
    )


# ---------------------------------------------------------------------- OTP
async def otp_get(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        client = await _get_client(ctx)
        me = await client.get_me()
    except Exception as e:
        await q.edit_message_text(
            f"❌ Connection failed: `{e}`", reply_markup=manage_dash()
        )
        return
    await q.edit_message_text(
        f"📱 Phone      : `+{me.phone}`\n"
        f"👤 Account    : {me.first_name or 'Unknown'}\n\n"
        "Tap the button to read the latest OTP from this account.",
        reply_markup=otp_menu(),
    )


async def otp_read(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        client = await _get_client(ctx)
        result = await session_service.read_otp(client)
    except Exception as e:
        await q.edit_message_text(
            f"❌ OTP read failed: `{e}`", reply_markup=otp_menu()
        )
        return
    if not result:
        await q.edit_message_text(
            "❌ No OTP found in recent chats.\n\n"
            "Make sure the account recently received a login code.",
            reply_markup=otp_menu(),
        )
        return
    date, code, chat = result
    await q.edit_message_text(
        f"🔑 *OTP FOUND*\n\n"
        f"Code     : `{code}`\n"
        f"Chat     : {chat}\n"
        f"Received : {date:%Y-%m-%d %H:%M:%S} UTC",
        reply_markup=otp_menu(),
    )


# ------------------------------------------------------------------ change mail
async def mail_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "📧 *Change Login Email*\n\n"
        f"This will change the account's login email to:\n`{GUARD_EMAIL}`\n\n"
        "The verification code is read automatically from the email inbox.\n"
        "Continue?",
        reply_markup=confirm_mail(),
    )


async def mail_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        client = await _get_client(ctx)
    except Exception as e:
        await q.edit_message_text(
            f"❌ Connection failed: `{e}`", reply_markup=manage_dash()
        )
        return
    status = await q.edit_message_text("📧 Sending verification code...")
    try:
        await session_service.change_email(
            client, GUARD_EMAIL, GUARD_EMAIL_APP_PASSWORD
        )
    except Exception as e:
        await status.edit_text(
            f"❌ Email change failed:\n`{e}`", reply_markup=manage_dash()
        )
        return
    await status.edit_text(
        f"✅ *Login email changed to*\n`{GUARD_EMAIL}`\n\n"
        "The account now uses the new email for login & recovery.",
        reply_markup=manage_dash(),
    )


async def mail_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "✅ Cancelled — email unchanged.", reply_markup=manage_dash()
    )


# ------------------------------------------------------------------ registration
def register(app):
    app.add_handler(CallbackQueryHandler(ma, pattern="^ma$"))
    app.add_handler(CallbackQueryHandler(dash, pattern="^dash$"))
    app.add_handler(CallbackQueryHandler(dev_list, pattern="^dev_list$"))
    app.add_handler(CallbackQueryHandler(dev_confirm, pattern=r"^dev_\d+$"))
    app.add_handler(CallbackQueryHandler(dev_yes, pattern=r"^dev_yes_\d+$"))
    app.add_handler(CallbackQueryHandler(dev_no, pattern=r"^dev_no_\d+$"))
    app.add_handler(CallbackQueryHandler(revoke_ask, pattern="^revoke_ask$"))
    app.add_handler(CallbackQueryHandler(revoke_yes, pattern="^revoke_yes$"))
    app.add_handler(CallbackQueryHandler(revoke_no, pattern="^revoke_no$"))
    app.add_handler(CallbackQueryHandler(clear_ask, pattern="^clear_ask$"))
    app.add_handler(CallbackQueryHandler(clear_yes, pattern="^clear_yes$"))
    app.add_handler(CallbackQueryHandler(clear_no, pattern="^clear_no$"))
    app.add_handler(CallbackQueryHandler(otp_get, pattern="^otp_get$"))
    app.add_handler(CallbackQueryHandler(otp_read, pattern="^otp_read$"))
    app.add_handler(CallbackQueryHandler(mail_ask, pattern="^mail_ask$"))
    app.add_handler(CallbackQueryHandler(mail_yes, pattern="^mail_yes$"))
    app.add_handler(CallbackQueryHandler(mail_no, pattern="^mail_no$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, hex_received))
