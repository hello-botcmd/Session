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
from bot.utils.helpers import (
    device_hash,
    fmt_account_card,
    fmt_device,
    fmt_phone,
    h,
    is_hex,
)


def _dash_msg(info: dict) -> str:
    return (
        "🧰 <b>MANAGE DASHBOARD</b>\n\n"
        f"📱 Phone : <code>{h(fmt_phone(info.get('phone')))}</code>\n"
        f"👤 Name  : {h(info.get('name') or 'Unknown')}\n\n"
        "Choose an option:"
    )


async def _get_client(ctx: ContextTypes.DEFAULT_TYPE, hex_str: str | None = None):
    guard = ctx.bot_data["guard"]
    hex_str = hex_str or ctx.user_data.get("hex")
    if hex_str and guard.is_guarded(hex_str):
        return await guard.get_client(hex_str)

    client = ctx.user_data.get("client")
    if not client:
        if not hex_str:
            raise ValueError("No active session")
        client = await session_service.make_client(hex_str)
        ctx.user_data["client"] = client
    elif not client.is_connected():
        await client.connect()
    return client


async def ma(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.bot_data["state"].set_wait(update.effective_user.id, "manage")
    await q.edit_message_text(
        "🧰 <b>Manage Account</b>\n\n"
        "Send a Telegram session hex or StringSession.\n\n"
        "Supported: Telethon / Pyrogram string, packed hex, bare auth_key hex.",
        parse_mode="HTML",
        reply_markup=cancel_only(),
    )


async def hex_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    state = ctx.bot_data["state"]
    mode = state.waiting(uid)
    if not mode:
        return
    text = (update.message.text or "").strip()
    if not is_hex(text):
        await update.message.reply_text(
            "⚠️ That does not look like a session.\n\n"
            "Send a long hex string or a Telethon/Pyrogram session string.",
            parse_mode="HTML",
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
    msg = await update.message.reply_text("⏳ Verifying session and checking SpamBot...")
    try:
        info, client = await session_service.verify(hex_str)
    except Exception as e:
        await msg.edit_text(
            f"❌ <b>Verification failed</b>\n\n"
            f"<code>{h(type(e).__name__)}: {h(e)}</code>\n\n"
            "The session may be expired, revoked or invalid.",
            parse_mode="HTML",
            reply_markup=cancel_only(),
        )
        return

    old = ctx.user_data.pop("client", None)
    if old and old is not client:
        try:
            await old.disconnect()
        except Exception:
            pass

    ctx.user_data["client"] = client
    ctx.user_data["hex"] = hex_str
    ctx.bot_data["state"].set_hex(uid, hex_str)

    session_string = info.get("session_string") or await session_service.get_session_string(hex_str)
    await ctx.bot_data["accounts"].upsert(
        hex_str,
        {
            "phone": info["phone"],
            "name": info["name"],
            "user_id": info["user_id"],
            "spam": info["spam"],
            "spam_detail": info.get("spam_detail", ""),
            "devices": info.get("device_count", 0),
            "active": False,
            "session_string": session_string,
            "verified_at": datetime.utcnow(),
        },
        owner_id=uid,
    )

    await msg.edit_text(fmt_account_card(info), parse_mode="HTML")
    await update.message.reply_text(
        _dash_msg(info),
        parse_mode="HTML",
        reply_markup=manage_dash(),
    )


async def dash(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    hex_str = ctx.bot_data["state"].get_hex(uid)
    if not hex_str:
        await q.edit_message_text(
            "❌ No active session. Start again from <b>Manage Account</b>.",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return
    try:
        client = await _get_client(ctx, hex_str)
        info = await session_service.account_summary(client)
    except Exception:
        info = {}
    await q.edit_message_text(
        _dash_msg(info),
        parse_mode="HTML",
        reply_markup=manage_dash(),
    )


async def dev_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        client = await _get_client(ctx)
        devices = await session_service.list_devices(client)
    except Exception as e:
        await q.edit_message_text(
            f"❌ Failed to fetch devices: <code>{h(e)}</code>",
            parse_mode="HTML",
            reply_markup=manage_dash(),
        )
        return
    text = (
        f"📱 <b>DEVICE DASHBOARD</b>\n\n"
        f"Total sessions: {len(devices)}\n\n"
        "Tap a device to terminate it:"
    )
    await q.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=device_rows(devices),
    )


async def dev_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        hsh = int(q.data.split("_", 1)[1])
    except (IndexError, ValueError):
        await q.edit_message_text("❌ Invalid device.", parse_mode="HTML", reply_markup=manage_dash())
        return
    try:
        client = await _get_client(ctx)
        devices = await session_service.list_devices(client)
        dev = next((a for a in devices if device_hash(a) == hsh), None)
    except Exception:
        dev = None
    if not dev:
        await q.edit_message_text(
            "❌ Device not found. Refresh the device list.",
            parse_mode="HTML",
            reply_markup=manage_dash(),
        )
        return
    if dev.get("current"):
        await q.edit_message_text(
            "⚠️ That is the bot's current session. Use <b>Revoke Bot Session</b> instead.",
            parse_mode="HTML",
            reply_markup=device_rows(devices),
        )
        return
    await q.edit_message_text(
        f"⚠️ <b>Terminate this device?</b>\n\n{fmt_device(dev)}\n\n"
        "The session will be logged out on that device.",
        parse_mode="HTML",
        reply_markup=confirm_dev(hsh),
    )


async def dev_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        hsh = int(q.data.rsplit("_", 1)[-1])
    except ValueError:
        await q.edit_message_text("❌ Invalid device.", parse_mode="HTML", reply_markup=manage_dash())
        return
    try:
        client = await _get_client(ctx)
        await session_service.terminate_device(client, hsh)
        devices = await session_service.list_devices(client)
    except Exception as e:
        await q.edit_message_text(
            f"❌ Failed to terminate device: <code>{h(e)}</code>",
            parse_mode="HTML",
            reply_markup=manage_dash(),
        )
        return
    await q.edit_message_text(
        f"✅ <b>Device terminated.</b>\n\nRemaining devices: {len(devices)}",
        parse_mode="HTML",
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
        parse_mode="HTML",
        reply_markup=device_rows(devices),
    )


async def revoke_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🔐 <b>Revoke Bot Session</b>\n\n"
        "This logs out only the bot's own session from this account.\n"
        "After that you must re-verify with a fresh session.",
        parse_mode="HTML",
        reply_markup=confirm_revoke(),
    )


async def revoke_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    hex_str = ctx.user_data.pop("hex", None)
    ctx.bot_data["state"].clear_hex(uid)
    guard = ctx.bot_data["guard"]
    if hex_str and guard.is_guarded(hex_str):
        try:
            await guard.stop_guard(hex_str, logout=True)
        except Exception:
            pass
        client = ctx.user_data.pop("client", None)
    else:
        client = ctx.user_data.pop("client", None)
        if client:
            try:
                await session_service.revoke_session(client)
            except Exception:
                try:
                    await client.disconnect()
                except Exception:
                    pass
    if hex_str:
        await ctx.bot_data["accounts"].delete(hex_str, owner_id=uid)
    await q.edit_message_text(
        "✅ <b>Bot session revoked.</b>\n\n"
        "The connection is logged out and removed from your storage.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


async def revoke_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "✅ Cancelled — session kept.",
        parse_mode="HTML",
        reply_markup=manage_dash(),
    )


async def clear_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🧹 <b>Clear All</b>\n\n"
        "This will wipe everything on the account:\n"
        "• All contacts\n"
        "• All private chats &amp; DMs (revoked)\n"
        "• All groups &amp; channels (left/deleted)\n\n"
        "⚠️ This is <b>irreversible</b>. Continue?",
        parse_mode="HTML",
        reply_markup=confirm_clear(),
    )


async def clear_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        client = await _get_client(ctx)
    except Exception as e:
        await q.edit_message_text(
            f"❌ Connection failed: <code>{h(e)}</code>",
            parse_mode="HTML",
            reply_markup=manage_dash(),
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
            f"❌ Clear failed: <code>{h(e)}</code>",
            parse_mode="HTML",
            reply_markup=manage_dash(),
        )
        return

    await progress.edit_text(
        "✅ <b>Account cleared!</b>\n\n"
        f"👤 Contacts deleted : {stats['contacts']}\n"
        f"💬 Chats deleted    : {stats['dialogs']}\n"
        f"👥 Groups left      : {stats['groups']}\n"
        f"📢 Channels deleted : {stats['channels']}\n\n"
        "All done.",
        parse_mode="HTML",
        reply_markup=manage_dash(),
    )


async def clear_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "✅ Cancelled — nothing was touched.",
        parse_mode="HTML",
        reply_markup=manage_dash(),
    )


async def otp_get(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        client = await _get_client(ctx)
        me = await client.get_me()
    except Exception as e:
        await q.edit_message_text(
            f"❌ Connection failed: <code>{h(e)}</code>",
            parse_mode="HTML",
            reply_markup=manage_dash(),
        )
        return
    await q.edit_message_text(
        f"📱 Phone   : <code>{h(fmt_phone(me.phone))}</code>\n"
        f"👤 Account : {h(me.first_name or 'Unknown')}\n\n"
        "Tap the button to read the latest OTP from this account.",
        parse_mode="HTML",
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
            f"❌ OTP read failed: <code>{h(e)}</code>",
            parse_mode="HTML",
            reply_markup=otp_menu(),
        )
        return
    if not result:
        await q.edit_message_text(
            "❌ No OTP found in recent chats.\n\n"
            "Make sure the account recently received a login code.",
            parse_mode="HTML",
            reply_markup=otp_menu(),
        )
        return
    date, code, chat = result
    await q.edit_message_text(
        "🔑 <b>OTP FOUND</b>\n\n"
        f"Code     : <code>{h(code)}</code>\n"
        f"Chat     : {h(chat)}\n"
        f"Received : {date:%Y-%m-%d %H:%M:%S} UTC",
        parse_mode="HTML",
        reply_markup=otp_menu(),
    )


async def mail_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    mail = await ctx.bot_data["mails"].get(update.effective_user.id)
    if not mail:
        await q.edit_message_text(
            "📧 <b>No mailbox saved</b>\n\n"
            "Save yours first:\n"
            "<code>/addmail you@gmail.com ---- app-password</code>",
            parse_mode="HTML",
            reply_markup=manage_dash(),
        )
        return
    await q.edit_message_text(
        "📧 <b>Change Login Email</b>\n\n"
        f"This will change the account's login email to:\n<code>{h(mail.get('email'))}</code>\n\n"
        "The verification code is read automatically from that inbox.\n"
        "Continue?",
        parse_mode="HTML",
        reply_markup=confirm_mail(),
    )


async def mail_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    mail = await ctx.bot_data["mails"].get(update.effective_user.id)
    if not mail:
        await q.edit_message_text(
            "❌ No mailbox saved. Use /addmail first.",
            parse_mode="HTML",
            reply_markup=manage_dash(),
        )
        return
    try:
        client = await _get_client(ctx)
    except Exception as e:
        await q.edit_message_text(
            f"❌ Connection failed: <code>{h(e)}</code>",
            parse_mode="HTML",
            reply_markup=manage_dash(),
        )
        return
    status = await q.edit_message_text("📧 Sending verification code...")
    try:
        await session_service.change_email(client, mail["email"], mail["app_password"])
    except Exception as e:
        await status.edit_text(
            f"❌ Email change failed:\n<code>{h(e)}</code>",
            parse_mode="HTML",
            reply_markup=manage_dash(),
        )
        return
    await status.edit_text(
        f"✅ <b>Login email changed to</b>\n<code>{h(mail['email'])}</code>\n\n"
        "The account now uses this email for login &amp; recovery.",
        parse_mode="HTML",
        reply_markup=manage_dash(),
    )


async def mail_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "✅ Cancelled — email unchanged.",
        parse_mode="HTML",
        reply_markup=manage_dash(),
    )


def register(app):
    app.add_handler(CallbackQueryHandler(ma, pattern="^ma$"))
    app.add_handler(CallbackQueryHandler(dash, pattern="^dash$"))
    app.add_handler(CallbackQueryHandler(dev_list, pattern="^dev_list$"))
    app.add_handler(CallbackQueryHandler(dev_confirm, pattern=r"^dev_-?\d+$"))
    app.add_handler(CallbackQueryHandler(dev_yes, pattern=r"^dev_yes_-?\d+$"))
    app.add_handler(CallbackQueryHandler(dev_no, pattern=r"^dev_no_-?\d+$"))
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
