"""Telegram bot managing channel menus, All-Access bundles, profile views, and support."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import Plan, get_settings
from database import (
    create_pending_payment,
    ensure_utc,
    get_all_user_subscriptions,
    session_scope,
    upsert_user,
)
from mpesa import MpesaError, mask_msisdn, mpesa_client, normalize_msisdn

logger = logging.getLogger(__name__)

SELECTING_CHANNEL, SELECTING_PLAN, AWAITING_PHONE = range(3)
_stk_locks: dict[int, asyncio.Lock] = {}


def _lock_for(telegram_id: int) -> asyncio.Lock:
    if telegram_id not in _stk_locks:
        _stk_locks[telegram_id] = asyncio.Lock()
    return _stk_locks[telegram_id]


def _format_expiry(when: datetime) -> str:
    return ensure_utc(when).astimezone(timezone.utc).strftime("%d-%b-%Y %H:%M EAT")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if user is None or update.message is None:
        return ConversationHandler.END

    user_name = user.first_name if user.first_name else "Subscriber"
    settings = get_settings()

    async with session_scope() as session:
        await upsert_user(session, user.id, user.username, user.first_name)

    # 🌟 CORE MAIN MENU INTERFACE GRID
    keyboard = []
    for ch_id, name in settings.channels.items():
        keyboard.append([InlineKeyboardButton(f"{name}", callback_data=f"chan:{ch_id}")])
        
    # Appends standard tracking utility buttons panel row
    keyboard.append([
        InlineKeyboardButton("👤 My Account", callback_data="nav_account"),
        InlineKeyboardButton("💬 Help Support", url=f"https://t.me{settings.support_admin_username}")
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"Hello {user_name}\n"
        "🔥 **WELCOME TO SABLENYA HUB VIP ACCESS MASTER PORTAL** ❤️\n\n"
        "Select an independent Premium Channel or choose the **All-Access Bundle** to instantly unlock all communities at a massive discount! 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )
    return SELECTING_CHANNEL


async def channel_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.data is None:
        return ConversationHandler.END
    await query.answer()

    if query.data == "nav_account":
        await view_account_profile(query)
        return SELECTING_CHANNEL

    channel_id = int(query.data.split(":", 1)[1])
    context.user_data["target_channel_id"] = channel_id
    settings = get_settings()
    channel_name = settings.channels.get(channel_id, "Premium Access Hub")

    rows = [
        [InlineKeyboardButton(plan.name, callback_data=f"plan:{plan.code}")]
        for plan in settings.plans.values() if plan.channel_id == channel_id
    ]
    rows.append([InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_home")])
    reply_markup = InlineKeyboardMarkup(rows)

    await query.edit_message_text(
        text=f"🛒 *Selected Target:* {channel_name}\n\nChoose your preferred access tier duration package below 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )
    return SELECTING_PLAN


async def view_account_profile(query) -> None:
    """Queries active database subscription records to compile real-time membership listings."""
    user_id = query.from_user.id
    settings = get_settings()
    
    async with session_scope() as session:
        active_subs = await get_all_user_subscriptions(session, user_id)
        
    profile_text = "👤 **YOUR MEMBERSHIP ACCOUNT PROFILE**\n\n"
    if not active_subs:
        profile_text += "❌ You do not have any active Premium Subscriptions at the moment."
    else:
        profile_text += "✅ You currently have active access to the following premium communities:\n\n"
        for sub in active_subs:
            ch_name = settings.channels.get(sub.channel_id, "Premium Channel")
            profile_text += f"▪️ **{ch_name}**\n   ⏱️ Expires: `{_format_expiry(sub.expires_at)}`\n\n"
            
    keyboard = [[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_home")]]
    await query.edit_message_text(text=profile_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))


async def plan_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.data is None:
        return ConversationHandler.END
    await query.answer()
    
    if query.data == "nav_home":
        settings = get_settings()
        keyboard = [[InlineKeyboardButton(f"{name}", callback_data=f"chan:{ch_id}")] for ch_id, name in settings.channels.items()]
        keyboard.append([InlineKeyboardButton("👤 My Account", callback_data="nav_account"), InlineKeyboardButton("💬 Help Support", url=f"https://t.me{settings.support_admin_username}")])
        await query.edit_message_text(text="Select a Premium Channel or choose the **All-Access Bundle** to proceed: 👇", reply_markup=InlineKeyboardMarkup(keyboard))
        return SELECTING_CHANNEL

    code = query.data.split(":", 1)[1]
    settings = get_settings()
    plan = settings.plan_by_code(code)
    if plan is None:
        await query.edit_message_text("That package tier is offline. Run /start.")
        return ConversationHandler.END

    context.user_data["plan_code"] = plan.code
    hint = ""
    user = update.effective_user
    if user:
        async with session_scope() as session:
            db_user = await upsert_user(session, user.id, user.username, user.first_name)
            if db_user.phone:
                hint = f"\nLast number on file: `{mask_msisdn(db_user.phone)}`"

    await query.edit_message_text(
        f"Selected Pack: *{plan.name}*\n\n"
        "Please enter your M-Pesa phone number to trigger your checkout STK Push prompt screen.\n"
        "Accepted format examples: `0712345678` or `254712345678`."
        f"{hint}\n\nSend /cancel to close menu.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return AWAITING_PHONE


async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    user = update.effective_user
    if message is None or user is None or message.text is None:
        return AWAITING_PHONE

    plan_code = context.user_data.get("plan_code")
    channel_id = context.user_data.get("target_channel_id")
    settings = get_settings()
    plan = settings.plan_by_code(plan_code) if plan_code else None
    
    if plan is None or channel_id is None:
        await message.reply_text("Session timeout. Please run /start to restart.")
        return ConversationHandler.END

    phone = normalize_msisdn(message.text)
    if phone is None:
        await message.reply_text("Invalid structure format. Supply a valid Kenyan mobile line number:")
        return AWAITING_PHONE

    await message.reply_text(f"🔄 Dispatched an M-Pesa push prompt token to screen {mask_msisdn(phone)}. Complete authorization by keying in your PIN code.")
    try:
        await _launch_stk(user.id, channel_id, plan, phone)
    except MpesaError as exc:
        await message.reply_text(f"Could not initialize mobile push: {exc}. Run /start to retry.")
        return ConversationHandler.END
    except Exception:
        logger.exception("STK push connection failure")
        await message.reply_text("Something went wrong while contacting Safaricom. Try again shortly.")
        return ConversationHandler.END

    context.user_data.clear()
    return ConversationHandler.END


async def _launch_stk(telegram_id: int, channel_id: int, plan: Plan, phone: str) -> None:
    async with _lock_for(telegram_id):
        resp = await mpesa_client.trigger_stk_push(phone=phone, amount=plan.amount, account_ref=f"SUB{telegram_id}")
        async with session_scope() as session:
            await create_pending_payment(session=session, telegram_id=telegram_id, channel_id=channel_id, plan_code=plan.code, phone=phone, checkout_id=resp["CheckoutRequestID"])


def build_application() -> Application:
    settings = get_settings()
    application = Application.builder().token(settings.telegram_bot_token).build()
    
    conversation = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECTING_CHANNEL: [CallbackQueryHandler(channel_selected, pattern=r"^(chan:|nav_account)")],
            SELECTING_PLAN: [CallbackQueryHandler(plan_selected, pattern=r"^(plan:|nav_home)")],
            AWAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )
    
    application.add_handler(conversation)
    return application


async def fulfill_successful_payment(telegram_id: int, channel_id: int, plan_code: str, expires_at: datetime) -> None:
    settings = get_settings()
    plan = settings.plan_by_code(plan_code)
    plan_name = plan.name if plan else plan_code
    expiry_str = _format_expiry(expires_at)
    
    async with Bot(token=settings.telegram_bot_token) as bot_client:
        try:
                        # 👑 BUNDLE LOGIC UNLOCKER: Generate single-use join keys for all channels
            if channel_id == 999999:
                success_msg = f"👑 **ALL-ACCESS VIP BUNDLE PURCHASE CONFIRMED!** 🎉\n\nYour master membership is active. Use your private single-use invite links below to enter each individual channel:\n\n"
                for target_ch_id, name in settings.channels.items():
                    if target_ch_id != 999999:
                        link_obj = await bot_client.create_chat_invite_link(chat_id=target_ch_id, member_limit=1, expire_date=datetime.now(timezone.utc) + timedelta(hours=settings.invite_link_ttl_hours))
                        success_msg += f"▪️ **{name}**\n🔗 {link_obj.invite_link}\n\n"
                success_msg += f"⏰ **All Access Period Expires:** `{expiry_str}`"
                await bot_client.send_message(chat_id=telegram_id, text=success_msg, parse_mode=ParseMode.MARKDOWN)
            else:
                channel_name = settings.channels.get(channel_id, "Premium Community")
                invite_link_obj = await bot_client.create_chat_invite_link(chat_id=channel_id, member_limit=1, expire_date=datetime.now(timezone.utc) + timedelta(hours=settings.invite_link_ttl_hours))
                await bot_client.send_message(
                    chat_id=telegram_id,
                    text=f"✅ *Payment Confirmed!*\n\nYour subscription for *{plan_name}* is now active.\nTarget Channel: *{channel_name}*\n\n🔗 *Single-use Join Link:* {invite_link_obj.invite_link}\n⏰ *Access Expires:* `{expiry_str}`\n\n_Note: Links are strictly single-use and valid for 24 hours._",
                    parse_mode=ParseMode.MARKDOWN
                )
        except TelegramError as e:
            logger.error(f"Invite link generation error context: {e}")


async def notify_failed_payment(telegram_id: int, plan_code: str, reason: str) -> None:
    settings = get_settings()
    plan = settings.plan_by_code(plan_code)
    plan_name = plan.name if plan else plan_code
    async with Bot(token=settings.telegram_bot_token) as bot_client:
        try:
            await bot_client.send_message(chat_id=telegram_id, text=f"❌ *M-Pesa Payment Failed*\n\nYour transaction setup for *{plan_name}* was cancelled.\n⚠️ *Reason:* `{reason}`\n\nRun /start to retry.", parse_mode=ParseMode.MARKDOWN)
        except TelegramError:
            pass


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("Cancelled. Send /start when you are ready to subscribe.")
    return ConversationHandler.END