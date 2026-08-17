from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from bot.keyboards.reply_markups import main_menu

WELCOME = (
    "🤖 *Welcome to Telegram Session Manager*\n\n"
    "Manage, guard and monitor your Telegram sessions — all from one bot.\n\n"
    "🚀 *Features:*\n"
    "🧰 *Manage Account* — verify any session hex, view account info, manage "
    "devices, wipe everything, fetch OTPs, change login email\n"
    "🛡️ *Guard Account* — keep a session alive 24/7 and instantly kick any "
    "other login within 2 seconds\n"
    "👤 *My Accounts* — dashboard of all stored/guarded accounts, fetch OTPs, "
    "revoke connections, temporary 60s login windows\n\n"
    "Select an option below 👇"
)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, reply_markup=main_menu())


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    state = ctx.bot_data["state"]
    state.clear_wait(update.effective_user.id)
    state.clear_hex(update.effective_user.id)

    client = ctx.user_data.pop("client", None)
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass

    await q.edit_message_text(
        "✅ Cancelled. Back to main menu.", reply_markup=main_menu()
    )


def register(app):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(cancel, pattern="^cancel$"))
