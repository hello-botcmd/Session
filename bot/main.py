import logging

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from config import BOT_TOKEN
from bot.handlers import start, manage, guard, my_accounts
from bot.services.account_service import AccountService
from bot.services.guard_service import GuardManager
from bot.utils.state import UserState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

async def fallback_cb(update: Update, ctx):
    q = update.callback_query
    if q.data == "noop":
        await q.answer("No action available")

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.bot_data["accounts"] = AccountService()
    app.bot_data["guard"] = GuardManager(app.bot, app.bot_data["accounts"])
    app.bot_data["state"] = UserState()

    start.register(app)
    manage.register(app)
    guard.register(app)
    my_accounts.register(app)
    app.add_handler(CallbackQueryHandler(fallback_cb))  # unmatched callbacks

    print("🤖 Session Manager Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
