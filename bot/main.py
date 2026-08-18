import logging

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler

from bot.handlers import admin, guard, manage, my_accounts, start
from bot.services.account_service import AccountService
from bot.services.admin_service import AdminService
from bot.services.guard_service import GuardManager
from bot.services.mail_service import MailService
from bot.utils.state import UserState
from config import BOT_TOKEN, validate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


async def fallback_cb(update: Update, ctx):
    q = update.callback_query
    if not q:
        return
    if q.data == "noop":
        await q.answer()
        return
    await q.answer()


async def on_startup(app):
    accounts = app.bot_data["accounts"]
    guard_mgr = app.bot_data["guard"]
    restored = 0
    for acc in await accounts.list_active():
        hex_str = acc.get("hex")
        chat_id = acc.get("chat_id") or acc.get("owner_id")
        if not hex_str or not chat_id:
            continue
        try:
            await guard_mgr.start_guard(
                hex_str,
                int(chat_id),
                session_string=acc.get("session_string"),
            )
            restored += 1
        except Exception as exc:
            log.warning("Could not restore guard for %s: %s", acc.get("phone"), exc)
            try:
                await accounts.set_active(hex_str, False)
            except Exception:
                pass
    log.info("Restored %s guarded session(s)", restored)


async def on_shutdown(app):
    await app.bot_data["guard"].shutdown(logout=False)


def main():
    validate()
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.bot_data["accounts"] = AccountService()
    app.bot_data["admins"] = AdminService()
    app.bot_data["mails"] = MailService()
    app.bot_data["guard"] = GuardManager(app.bot, app.bot_data["accounts"])
    app.bot_data["state"] = UserState()

    admin.register(app)
    start.register(app)
    manage.register(app)
    guard.register(app)
    my_accounts.register(app)
    app.add_handler(CallbackQueryHandler(fallback_cb))

    print("Session Manager Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
