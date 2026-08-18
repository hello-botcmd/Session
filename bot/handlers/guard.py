from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bot.keyboards.reply_markups import cancel_only, main_menu
from bot.utils.helpers import fmt_account_card, fmt_phone, h


async def guard_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.bot_data["state"].set_wait(update.effective_user.id, "guard")
    await q.edit_message_text(
        "🛡️ <b>Guard Account</b>\n\n"
        "Send the session you want to keep permanently active.\n\n"
        "The bot will:\n"
        "• Keep this login alive 24/7\n"
        "• Log out any other login within <b>2 seconds</b>\n"
        "• Notify you about every new login attempt\n\n"
        "Send the hex / session string:",
        parse_mode="HTML",
        reply_markup=cancel_only(),
    )


async def handle_hex(update: Update, ctx: ContextTypes.DEFAULT_TYPE, hex_str: str):
    msg = await update.message.reply_text("🛡️ Verifying session and checking SpamBot...")
    client = None
    try:
        from bot.services import session_service

        info, client = await session_service.verify(hex_str)
        session_string = info.get("session_string") or await session_service.get_session_string(hex_str)

        await msg.edit_text(
            fmt_account_card(info, title="Session verified") + "\n\n🛡️ Activating guard mode...",
            parse_mode="HTML",
        )

        # Reuse the same live client. A second connect on this auth_key
        # is AUTH_KEY_DUPLICATED and Telegram revokes the session.
        result = await ctx.bot_data["guard"].start_guard(
            hex_str,
            update.effective_chat.id,
            session_string=session_string,
            client=client,
        )
        client = None

        await ctx.bot_data["accounts"].upsert(
            hex_str,
            {
                "phone": info["phone"],
                "name": info["name"],
                "user_id": info["user_id"],
                "spam": info["spam"],
                "spam_detail": info.get("spam_detail", ""),
                "devices": info.get("device_count", 0),
                "active": True,
                "chat_id": update.effective_chat.id,
                "session_string": session_string,
            },
            owner_id=update.effective_user.id,
        )
    except Exception as e:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        await msg.edit_text(
            f"❌ Guard start failed:\n<code>{h(type(e).__name__)}: {h(e)}</code>",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    if result["status"] == "already":
        await msg.edit_text(
            "✅ This account is <b>already guarded</b> — nothing changed.",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    await msg.edit_text(
        "🛡️ <b>GUARD MODE ACTIVE</b>\n\n"
        f"📱 Phone   : <code>{h(fmt_phone(info.get('phone')))}</code>\n"
        f"👤 Name    : {h(info.get('name') or 'Unknown')}\n"
        f"⚠️ Status  : {h(info.get('spam') or 'Unknown')}\n\n"
        f"⚔️ Logged out {result['removed']} existing session(s)\n"
        "👁️ Watching for new logins every 2s...\n\n"
        "You will be notified of any login attempt.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


def register(app):
    app.add_handler(CallbackQueryHandler(guard_menu, pattern="^guard$"))
