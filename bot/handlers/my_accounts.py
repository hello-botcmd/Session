from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bot.keyboards.reply_markups import acc_dash, acc_page, confirm_revoke_acc, main_menu
from bot.services import session_service
from bot.utils.helpers import device_count, fmt_phone, h
from config import ALLOW_LOGIN_SECONDS, PAGE_SIZE


def _page_accs(all_accs, page):
    start = page * PAGE_SIZE
    return all_accs[start : start + PAGE_SIZE]


async def my_acc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data["acc_page"] = 0
    await _render_page(update, ctx, 0)


async def _render_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE, page: int):
    q = update.callback_query
    accounts = await ctx.bot_data["accounts"].all_for(update.effective_user.id)
    if not accounts:
        await q.edit_message_text(
            "👤 <b>My Accounts</b>\n\nNo stored accounts yet.\n\n"
            "Use <b>Manage Account</b> or <b>Guard Account</b> to add one.",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return
    total_pages = max(1, -(-len(accounts) // PAGE_SIZE))
    page = min(max(page, 0), total_pages - 1)
    ctx.user_data["acc_page"] = page
    shown = _page_accs(accounts, page)
    await q.edit_message_text(
        f"👤 <b>My Accounts</b> — page {page + 1}/{total_pages}\n\n"
        f"{len(accounts)} account(s) stored. Select one:",
        parse_mode="HTML",
        reply_markup=acc_page(shown, page, total_pages),
    )


async def acc_page_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    page = int(q.data.split("_")[1])
    await _render_page(update, ctx, page)


async def acc_open(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    oid = q.data.split("_", 1)[1]
    acc = await ctx.bot_data["accounts"].get_by_id(oid, owner_id=update.effective_user.id)
    if not acc:
        await q.edit_message_text(
            "❌ Account not found (maybe deleted).",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return
    ctx.user_data["acc_id"] = oid
    hex_str = acc.get("hex")
    guarded = ctx.bot_data["guard"].is_guarded(hex_str) if hex_str else False
    await q.edit_message_text(
        "👤 <b>ACCOUNT DASHBOARD</b>\n\n"
        f"📱 Phone   : <code>{h(fmt_phone(acc.get('phone')))}</code>\n"
        f"👤 Name    : {h(acc.get('name') or 'Unknown')}\n"
        f"🆔 User ID : <code>{h(acc.get('user_id') or 'Unknown')}</code>\n"
        f"📟 Devices : {device_count(acc.get('devices'))} connected\n"
        f"⚠️ Status  : {h(acc.get('spam') or 'Unknown')}\n"
        f"🛡️ Guard   : {'✅ Active' if guarded else '❌ Off'}\n\n"
        "Choose an action:",
        parse_mode="HTML",
        reply_markup=acc_dash(),
    )


async def acc_otp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    oid = ctx.user_data.get("acc_id")
    acc = await ctx.bot_data["accounts"].get_by_id(oid, owner_id=update.effective_user.id) if oid else None
    if not acc:
        await q.edit_message_text("❌ Account not found.", parse_mode="HTML", reply_markup=main_menu())
        return
    hex_str = acc.get("hex")
    status = await q.edit_message_text("🔑 Reading latest OTP...")
    client = None
    owned = False
    try:
        guard = ctx.bot_data["guard"]
        if hex_str and guard.is_guarded(hex_str):
            client = await guard.get_client(hex_str)
            owned = True
        else:
            client = await session_service.make_client(hex_str)
        result = await session_service.read_otp(client)
    except Exception as e:
        await status.edit_text(
            f"❌ OTP read failed: <code>{h(e)}</code>",
            parse_mode="HTML",
            reply_markup=acc_dash(),
        )
        return
    finally:
        if client and not owned:
            try:
                await client.disconnect()
            except Exception:
                pass
    if not result:
        await status.edit_text("❌ No OTP found in recent chats.", parse_mode="HTML", reply_markup=acc_dash())
        return
    date, code, chat = result
    await status.edit_text(
        "🔑 <b>OTP FOUND</b>\n\n"
        f"Code     : <code>{h(code)}</code>\n"
        f"Chat     : {h(chat)}\n"
        f"Received : {date:%Y-%m-%d %H:%M:%S} UTC",
        parse_mode="HTML",
        reply_markup=acc_dash(),
    )


async def acc_revoke_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🔌 <b>Revoke Bot Connection</b>\n\n"
        "This logs out the bot's session from this account and removes it "
        "from your stored list.\n\nContinue?",
        parse_mode="HTML",
        reply_markup=confirm_revoke_acc(),
    )


async def acc_revoke_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    oid = ctx.user_data.get("acc_id")
    acc = await ctx.bot_data["accounts"].get_by_id(oid, owner_id=update.effective_user.id) if oid else None
    if acc and acc.get("hex"):
        try:
            await ctx.bot_data["guard"].stop_guard(acc["hex"], logout=True)
        except Exception:
            pass
        await ctx.bot_data["accounts"].delete(acc["hex"], owner_id=update.effective_user.id)
    await q.edit_message_text(
        "✅ <b>Bot connection revoked</b> — session logged out and removed.",
        parse_mode="HTML",
        reply_markup=main_menu(),
    )


async def acc_revoke_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "✅ Cancelled — connection kept.",
        parse_mode="HTML",
        reply_markup=acc_dash(),
    )


async def acc_allow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    oid = ctx.user_data.get("acc_id")
    acc = await ctx.bot_data["accounts"].get_by_id(oid, owner_id=update.effective_user.id) if oid else None
    if not acc or not acc.get("hex"):
        await q.edit_message_text("❌ Account not found.", parse_mode="HTML", reply_markup=main_menu())
        return
    hex_str = acc["hex"]
    guard = ctx.bot_data["guard"]
    if not guard.is_guarded(hex_str):
        await q.edit_message_text(
            "❌ This account is not guarded, so there is nothing to allow login for.",
            parse_mode="HTML",
            reply_markup=acc_dash(),
        )
        return
    guard.allow_login(hex_str, seconds=ALLOW_LOGIN_SECONDS)
    await q.edit_message_text(
        "🔓 <b>LOGIN WINDOW OPENED</b>\n\n"
        f"Anyone can now log into this account for <b>{ALLOW_LOGIN_SECONDS} seconds</b>.\n"
        "When the window closes, guard mode re-activates and sessions created "
        "during the window are terminated.",
        parse_mode="HTML",
        reply_markup=acc_dash(),
    )


def register(app):
    app.add_handler(CallbackQueryHandler(my_acc, pattern="^myacc$"))
    app.add_handler(CallbackQueryHandler(acc_page_cb, pattern=r"^page_\d+$"))
    app.add_handler(CallbackQueryHandler(acc_open, pattern=r"^acc_"))
    app.add_handler(CallbackQueryHandler(acc_otp, pattern="^otp_acc$"))
    app.add_handler(CallbackQueryHandler(acc_revoke_ask, pattern="^revoke_acc$"))
    app.add_handler(CallbackQueryHandler(acc_revoke_yes, pattern="^revoke_acc_yes$"))
    app.add_handler(CallbackQueryHandler(acc_revoke_no, pattern="^revoke_acc_no$"))
    app.add_handler(CallbackQueryHandler(acc_allow, pattern="^allow_acc$"))
