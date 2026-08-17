from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bot.keyboards.reply_markups import cancel_only, main_menu
from bot.services import session_service
from bot.utils.helpers import fmt_phone, is_hex


async def guard_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.bot_data["state"].set_wait(update.effective_user.id, "guard")
    await q.edit_message_text(
        "🛡️ *Guard Account*\n\n"
        "Send the session hex you want to keep permanently active.\n\n"
        "The bot will:\n"
        "• Keep the login alive 24/7\n"
        "• Log out any other login within **2 seconds**\n"
        "• Notify you instantly about every new login attempt\n\n"
        "Send the hex string:",
        reply_markup=cancel_only(),
    )


async def handle_hex(update: Update, ctx: ContextTypes.DEFAULT_TYPE, hex_str: str):
    uid = update.effective_user.id
    msg = await update.message.reply_text("🛡️ Verifying & connecting session...")
    try:
        info, client = await session_service.verify(hex_str)
    except Exception as e:
        await msg.edit_text(
            f"❌ *Verification failed*\n\n`{type(e).__name__}: {e}`",
            reply_markup=cancel_only(),
        )
        return
    finally:
        try:
            client.disconnect()
        except Exception:
            pass

    await msg.edit_text(
        f"✅ *Session verified*\n\n"
        f"📱 Phone   : {fmt_phone(info.get('phone'))}\n"
        f"👤 Name    : {info.get('name') or 'Unknown'}\n"
        f"🆔 User ID : {info.get('user_id') or 'Unknown'}\n\n"
        "🛡️ Activating guard mode...",
    )

    guard = ctx.bot_data["guard"]
    try:
        result = await guard.start_guard(hex_str, update.effective_chat.id)
    except Exception as e:
        await msg.edit_text(
            f"❌ Guard start failed:\n`{e}`", reply_markup=main_menu()
        )
        return

    ctx.bot_data["accounts"].upsert(hex_str, {
        "phone": info["phone"],
        "name": info["name"],
        "user_id": info["user_id"],
        "spam": info["spam"],
        "devices": info["devices"],
        "active": True,
    })

    if result["status"] == "already":
        await msg.edit_text(
            "✅ This account is **already guarded** — nothing changed.",
            reply_markup=main_menu(),
        )
        return

    await msg.edit_text(
        f"🛡️ *GUARD MODE ACTIVE*\n\n"
        f"📱 Phone   : {fmt_phone(info.get('phone'))}\n"
        f"👤 Name    : {info.get('name') or 'Unknown'}\n"
        f"⚠️ Status  : {info.get('spam') or 'Unknown'}\n\n"
        f"⚔️ Logged out {result['removed']} existing session(s)\n"
        "👁️ Watching for new logins every 2s...\n\n"
        "You will be notified of any login attempt.",
        reply_markup=main_menu(),
    )


def register(app):
    app.add_handler(CallbackQueryHandler(guard_menu, pattern="^guard$"))
