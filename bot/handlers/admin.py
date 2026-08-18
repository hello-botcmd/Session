from telegram import Update
from telegram.ext import ApplicationHandlerStop, CommandHandler, ContextTypes, TypeHandler

from bot.keyboards.reply_markups import main_menu
from bot.utils.helpers import h


HELP = (
    "<b>Session Manager — commands</b>\n\n"
    "<b>Everyone authorized</b>\n"
    "/start — open the menu\n"
    "/help — this list\n"
    "/cancel — abort the current step\n"
    "/addmail email ---- app_password — save your IMAP mailbox\n"
    "/rmmail — delete your saved mailbox\n\n"
    "<b>Owner only</b>\n"
    "/sudo user_id — grant access\n"
    "/rmsudo user_id — revoke access\n"
    "/sudolist — list extra admins\n\n"
    "<b>Menu</b>\n"
    "🧰 Manage Account — verify session, devices, OTP, email, wipe\n"
    "🛡️ Guard Account — keep one session, kick every other login\n"
    "👤 My Accounts — your stored sessions, OTP, 60s login window"
)


async def auth_gate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user and await ctx.bot_data["admins"].is_sudo(user.id):
        return
    if update.callback_query:
        await update.callback_query.answer("Unauthorized", show_alert=True)
    elif update.message:
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
    raise ApplicationHandlerStop


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP, parse_mode="HTML", reply_markup=main_menu())


def _target_id(update: Update) -> int | None:
    parts = (update.message.text or "").split()
    if len(parts) < 2:
        return None
    raw = parts[1].strip()
    if raw.startswith("@"):
        return None
    if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
        return int(raw)
    return None


async def sudo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    admins = ctx.bot_data["admins"]
    if not admins.is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Owner only.")
        return
    uid = _target_id(update)
    if uid is None:
        await update.message.reply_text("Usage: <code>/sudo 123456789</code>", parse_mode="HTML")
        return
    added = await admins.add(uid, update.effective_user.id)
    if not added:
        await update.message.reply_text("That user is already an owner or sudo.")
        return
    await update.message.reply_text(f"✅ Granted sudo to <code>{uid}</code>", parse_mode="HTML")
    try:
        await ctx.bot.send_message(uid, "✅ You now have access to the session manager. Send /start")
    except Exception:
        pass


async def rmsudo_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    admins = ctx.bot_data["admins"]
    if not admins.is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Owner only.")
        return
    uid = _target_id(update)
    if uid is None:
        await update.message.reply_text("Usage: <code>/rmsudo 123456789</code>", parse_mode="HTML")
        return
    if admins.is_owner(uid):
        await update.message.reply_text("Cannot remove an owner.")
        return
    removed = await admins.remove(uid)
    if not removed:
        await update.message.reply_text("That user is not a sudo.")
        return
    await update.message.reply_text(f"✅ Removed sudo from <code>{uid}</code>", parse_mode="HTML")


async def sudolist_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    admins = ctx.bot_data["admins"]
    if not admins.is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ Owner only.")
        return
    from config import OWNER_IDS

    lines = ["<b>Owners</b>"]
    for oid in OWNER_IDS:
        lines.append(f"• <code>{oid}</code>")
    sudos = await admins.list_sudos()
    lines.append("")
    lines.append("<b>Sudos</b>")
    if not sudos:
        lines.append("• none")
    else:
        for row in sudos:
            lines.append(f"• <code>{row.get('user_id')}</code>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


def _parse_mail(text: str) -> tuple[str, str] | None:
    body = text.split(None, 1)
    if len(body) < 2:
        return None
    payload = body[1].strip()
    if "----" in payload:
        email, password = payload.split("----", 1)
    else:
        parts = payload.split(None, 1)
        if len(parts) <
      return None
    email, password = email.strip(), password.strip()
    if "@" not in email or not password:
        return None
    return email, password


async def addmail_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    parsed = _parse_mail(update.message.text or "")
    if not parsed:
        await update.message.reply_text(
            "Usage:\n<code>/addmail you@gmail.com ---- app-password</code>",
            parse_mode="HTML",
        )
        return
    email, password = parsed
    await ctx.bot_data["mails"].set(update.effective_user.id, email, password)
    await update.message.reply_text(
        f"✅ Mailbox saved: <code>{h(email)}</code>\n"
        "Manage → Change Email will use this inbox for the verification code.",
        parse_mode="HTML",
    )


async def rmmail_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    removed = await ctx.bot_data["mails"].delete(update.effective_user.id)
    if not removed:
        await update.message.reply_text("No mailbox stored for you.")
        return
    await update.message.reply_text("✅ Mailbox removed.")


def register(app):
    app.add_handler(TypeHandler(Update, auth_gate), group=-1)
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("sudo", sudo_cmd))
    app.add_handler(CommandHandler("rmsudo", rmsudo_cmd))
    app.add_handler(CommandHandler("sudolist", sudolist_cmd))
    app.add_handler(CommandHandler("addmail", addmail_cmd))
    app.add_handler(CommandHandler("rmmail", rmmail_cmd))
