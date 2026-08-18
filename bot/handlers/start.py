from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from bot.keyboards.reply_markups import main_menu

WELCOME = (
    "🤖 <b>Telegram Session Manager</b>\n\n"
    "Manage, guard and monitor sessions from one place.\n\n"
    "🧰 <b>Manage Account</b> — verify a session, devices, wipe, OTP, email\n"
    "🛡️ <b>Guard Account</b> — keep one session alive and kick others in ~2s\n"
    "👤 <b>My Accounts</b> — your stored accounts only\n\n"
    "Use /help for the full command list."
)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        WELCOME,
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    ctx.bot_data["state"].clear_all(uid)

    hex_str = ctx.user_data.get("hex")
    guard = ctx.bot_data["guard"]
    client = ctx.user_data.pop("client", None)
    if client and not (hex_str and guard.is_guarded(hex_str)):
        try:
            await client.disconnect()
        except Exception:
            pass
    ctx.user_data.pop("hex", None)

    await q.edit_message_text(
        "✅ Cancelled. Back to main menu.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ctx.bot_data["state"].clear_all(uid)
    hex_str = ctx.user_data.get("hex")
    guard = ctx.bot_data["guard"]
    client = ctx.user_data.pop("client", None)
    if client and not (hex_str and guard.is_guarded(hex_str)):
        try:
            await client.disconnect()
        except Exception:
            pass
    ctx.user_data.pop("hex", None)
    await update.message.reply_text(
        "✅ Cancelled. Back to main menu.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


def register(app):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CallbackQueryHandler(cancel, pattern="^cancel$"))
