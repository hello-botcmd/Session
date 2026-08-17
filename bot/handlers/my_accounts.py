from datetime import datetime
from bot.services import session_service
from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from bot.keyboards.reply_markups import (
    acc_dash,
    acc_page,
    confirm_revoke_acc,
    main_menu,
)
from bot.utils.helpers import fmt_phone
from config import PAGE_SIZE


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
    accounts = ctx.bot_data["accounts"].all()
    if not accounts:
        await q.edit_message_text(
            "👤 *My Accounts*\n\nNo stored accounts yet.\n\n"
            "Use *Manage Account* or *Guard Account* to add one.",
            reply_markup=main_menu(),
        )
        return
    total_pages = max(1, -(-len(accounts) // PAGE_SIZE))
    page = min(max(page, 0), total_pages - 1)
    ctx.user_data["acc_page"] = page
    shown = _page_accs(accounts, page)
    await q.edit_message_text(
        f"👤 *My Accounts* — page {page + 1}/{total_pages}\n\n"
        f"{len(accounts)} account(s) stored. Select one:",
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
    oid = q.data.split("_")[1]
    acc = ctx.bot_data["accounts"].get_by_id(oid)
    if not acc:
        await q.edit_message_text(
            "❌ Account not found (maybe deleted).", reply_markup=main_menu()
        )
        return
    ctx.user_data["acc_id"] = oid
    hex_str = acc.get("hex")
    guard = ctx.bot_data["guard"]
    guarded = guard.is_guarded(hex_str) if hex_str else False
    devices = acc.get("devices", "?")
    await q.edit_message_text(
        f"👤 *ACCOUNT DASHBOARD*\n\n"
        f"📱 Phone   : {fmt_phone(acc.get('phone'))}\n"
        f"👤 Name    : {acc.get('name') or 'Unknown'}\n"
        f"🆔 User ID : {acc.get('user_id') or 'Unknown'}\n"
        f"📟 Devices : {devices} connected\n"
        f"⚠️ Status  : {acc.get('spam') or 'Unknown'}\n"
        f"🛡️ Guard   : {'✅ Active' if guarded else '❌ Off'}\n\n"
        "Choose an action:",
        reply_markup=acc_dash(),
    )


async def acc_otp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    oid = ctx.user_data.get("acc_id")
    acc = ctx.bot_data["accounts"].get_by_id(oid) if oid else None
    if not acc:
        await q.edit_message_text("❌ Account not found.", reply_markup=main_menu())
        return
    hex_str = acc.get("hex")
    status = await q.edit_message_text("🔑 Reading latest OTP...")
    try:
        guard = ctx.bot_data["guard"]
        if hex_str and guard.is_guarded(hex_str):
            client = await guard.get_client(hex_str)
        else:
            client = await session_service.make_client(hex_str)
        result = await session_service.read_otp(client)
        if hex_str and not guard.is_guarded(hex_str):
            try:
                await client.disconnect()
            except Exception:
                pass
    except Exception as e:
        await status.edit_text(
            f"❌ OTP read failed: `{e}`", reply_markup=acc_dash()
        )
        return
    if not result:
        await status.edit_text(
            "❌ No OTP found in recent chats.", reply_markup=acc_dash()
        )
        return
    date, code, chat = result
    await status.edit_text(
        f"🔑 *OTP FOUND*\n\n"
        f"Code     : `{code}`\n"
        f"Chat     : {chat}\n"
        f"Received : {date:%Y-%m-%d %H:%M:%S} UTC",
        reply_markup=acc_dash(),
    )


async def acc_revoke_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🔌 *Revoke Bot Connection*\n\n"
        "This logs out the bot's session from this account and removes it "
        "from your stored list.\n\nContinue?",
        reply_markup=confirm_revoke_acc(),
    )


async def acc_revoke_yes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    oid = ctx.user_data.get("acc_id")
    acc = ctx.bot_data["accounts"].get_by_id(oid) if oid else None
    if acc and acc.get("hex"):
        guard = ctx.bot_data["guard"]
        try:
            await guard.stop_guard(acc["hex"], logout=True)
        except Exception:
            pass
        ctx.bot_data["accounts"].delete(acc["hex"])
    await q.edit_message_text(
        "✅ *Bot connection revoked* — session logged out and removed.",
        reply_markup=main_menu(),
    )


async def acc_revoke_no(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "✅ Cancelled — connection kept.", reply_markup=acc_dash()
    )


async def acc_allow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    oid = ctx.user_data.get("acc_id")
    acc = ctx.bot_data["accounts"].get_by_id(oid) if oid else None
    if not acc or not acc.get("hex"):
        await q.edit_message_text("❌ Account not found.", reply_markup=main_menu())
        return
    hex_str = acc["hex"]
    guard = ctx.bot_data["guard"]
    if not guard.is_guarded(hex_str):
        await q.edit_message_text(
            "❌ This account is not guarded, so there's nothing to allow "
            "login for.", reply_markup=acc_dash()
        )
        return
    guard.allow_login(hex_str, seconds=60)
    ctx.user_data["allow_hex"] = hex_str
    await q.edit_message_text(
        "🔓 *LOGIN WINDOW OPENED*\n\n"
        "Anyone can now log into this account for **60 seconds**.\n"
        "After the window closes, guard mode re-activates automatically "
        "and any session created during the window is terminated.",
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
