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

from config import get_settings
from database import (
    create_pending_payment,
    ensure_utc,
    get_all_user_subscriptions,
    session_scope,
    upsert_user,
)
from megapay import MpesaError, mask_msisdn, mpesa_client, normalize_msisdn

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

    # 🌟 CORE TRACKED WALK THROUGH GRID MENUS
    keyboard = [
        [InlineKeyboardButton("📺❤️ SOMALIA PORN❤️", callback_data="chan:ch1")],
        [InlineKeyboardButton("❤️ EXPOSEE💦📺", callback_data="chan:ch2")],
        [InlineKeyboardButton("📺 TRENDING LEAKS🔞💦", callback_data="chan:ch3")],
        [InlineKeyboardButton("📺🔞 KENYAN PORN⛔", callback_data="chan:ch4")],
        [InlineKeyboardButton("💎 ALL OF THE ABOVE❤️💎", callback_data="chan:999999")],
        [
            InlineKeyboardButton("👤 My Account", callback_data="nav_account"),
            InlineKeyboardButton("💬 Help Support", url=f"https://t.me{settings.support_admin_username}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"Hello {user_name}\n"
        "🔥 **WELCOME TO SABLENYA HUB VIP ACCESS MASTER PORTAL** ❤️\n\n"
        "Select an independent Premium Channel or choose the **All-Access Bundle** to instantly unlock all communities at a massive discount! 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )
    return SELECTING_CHANNEL


async def view_account_profile(query) -> None:
    """Queries active database subscription records to compile real-time membership listings."""
    user_id = query.from_user.id
    
    async with session_scope() as session:
        active_subs = await get_all_user_subscriptions(session, user_id)
        
    profile_text = "👤 **YOUR MEMBERSHIP ACCOUNT PROFILE**\n\n"
    if not active_subs:
        profile_text += "❌ You do not have any active Premium Subscriptions at the moment."
    else:
        profile_text += "✅ You currently have active access to premium channels.\n\n"
        for sub in active_subs:
            profile_text += f"▪️ Subscription Plan: `{sub.plan_code}`\n   ⏱️ Expires: `{_format_expiry(sub.expires_at)}`\n\n"
            
    keyboard = [[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_home")]]
    await query.edit_message_text(text=profile_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))


async def channel_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.data is None:
        return ConversationHandler.END
    await query.answer()

    if query.data == "nav_account":
        await view_account_profile(query)
        return SELECTING_CHANNEL

    # Separate out the chan prefix token
    target = query.data.split(":", 1)[1]
    
    # 🌟 CORE IMAGE MATCHED PRICING ROUTER
    if target == "999999":
        context.user_data["target_channel_id"] = 999999
        rows = [
            [InlineKeyboardButton("📅 1 Week — 7 days | 499 KSHS", callback_data="plan:bun_1w")],
            [InlineKeyboardButton("📅 1 MONTH — 30 days | 1299 KSHS", callback_data="plan:bun_1m")],
            [InlineKeyboardButton("📅 3 MONTHS — 90 days | 1999 KSHS", callback_data="plan:bun_3m")],
            [InlineKeyboardButton("📅 LIFETIME | 2499 KSHS", callback_data="plan:bun_lt")]
        ]
    elif target == "ch1":
        context.user_data["target_channel_id"] = -1004338264708
        rows = [
            [InlineKeyboardButton("📅 1 Week — 7 days | 199 KSHS", callback_data="plan:ch1_1w")],
            [InlineKeyboardButton("📅 1 MONTH — 30 days | 499 KSHS", callback_data="plan:ch1_1m")],
            [InlineKeyboardButton("📅 3 MONTHS — 90 days | 999 KSHS", callback_data="plan:ch1_3m")],
            [InlineKeyboardButton("📅 LIFETIME | 1299 KSHS", callback_data="plan:ch1_lt")]
        ]
    elif target == "ch2":
        context.user_data["target_channel_id"] = -1004338264708
        rows = [
            [InlineKeyboardButton("📅 1 Week — 7 days | 399 KSHS", callback_data="plan:ch2_1w")],
            [InlineKeyboardButton("📅 1 MONTH — 30 days | 699 KSHS", callback_data="plan:ch2_1m")],
            [InlineKeyboardButton("📅 3 MONTHS — 90 days | 999 KSHS", callback_data="plan:ch2_3m")],
            [InlineKeyboardButton("📅 LIFETIME | 1499 KSHS", callback_data="plan:ch2_lt")]
        ]
    elif target == "ch4":
        context.user_data["target_channel_id"] = -1004338264708
        rows = [
            [InlineKeyboardButton("📅 1 Week — 7 days | 199 KSHS", callback_data="plan:ch4_1w")],
            [InlineKeyboardButton("📅 1 MONTH — 30 days | 499 KSHS", callback_data="plan:ch4_1m")],
            [InlineKeyboardButton("📅 3 MONTHS — 90 days | 999 KSHS", callback_data="plan:ch4_3m")],
            [InlineKeyboardButton("📅 LIFETIME | 1499 KSHS", callback_data="plan:ch4_lt")]
        ]
    else:
        # Default target fallback (📺 TRENDING LEAKS🔞💦)
        context.user_data["target_channel_id"] = -1004338264708
        rows = [
            [InlineKeyboardButton("📅 1 Week — 7 days | 399 KSHS", callback_data="plan:ch3_1w")],
            [InlineKeyboardButton("📅 1 MONTH — 30 days | 699 KSHS", callback_data="plan:ch3_1m")],
            [InlineKeyboardButton("📅 3 MONTHS — 90 days | 1299 KSHS", callback_data="plan:ch3_3m")],
            [InlineKeyboardButton("📅 LIFETIME | 1999 KSHS", callback_data="plan:ch3_lt")]
        ]
        
    rows.append([InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_home")])
    reply_markup = InlineKeyboardMarkup(rows)

    await query.edit_message_text(
        text="🛒 *Subscription Portal Matrix*\n\nChoose your preferred access tier duration package below 👇",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )
    return SELECTING_PLAN


async def plan_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.data is None:
        return ConversationHandler.END
    await query.answer()
    
    if query.data == "nav_home":
        settings = get_settings()
        keyboard = [
            [InlineKeyboardButton("📺❤️ SOMALIA PORN❤️", callback_data="chan:ch1")],
            [InlineKeyboardButton("❤️ EXPOSEE💦📺", callback_data="chan:ch2")],
            [InlineKeyboardButton("📺 TRENDING LEAKS🔞💦", callback_data="chan:ch3")],
            [InlineKeyboardButton("📺🔞 KENYAN PORN⛔", callback_data="chan:ch4")],
            [InlineKeyboardButton("💎 ALL OF THE ABOVE❤️💎", callback_data="chan:999999")],
            [InlineKeyboardButton("👤 My Account", callback_data="nav_account"), InlineKeyboardButton("💬 Help Support", url=f"https://t.me{settings.support_admin_username}")]
        ]
        await query.edit_message_text(text="Select an independent Premium Channel or choose the **All-Access Bundle** to proceed: 👇", reply_markup=InlineKeyboardMarkup(keyboard))
        return SELECTING_CHANNEL

    code = query.data.split(":", 1)[1]
    context.user_data["plan_code"] = code
    hint = ""
    user = update.effective_user
    if user:
        async with session_scope() as session:
            db_user = await upsert_user(session, user.id, user.username, user.first_name)
            if db_user.phone:
                hint = f"\nLast number on file: `{mask_msisdn(db_user.phone)}`"

    await query.edit_message_text(
        f"Selected Pack Reference: *{code.upper()}*\n\n"
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
    
    if plan_code is None or channel_id is None:
        await message.reply_text("Session timeout. Please run /start to restart.")
        return ConversationHandler.END

    phone = normalize_msisdn(message.text)
    if phone is None:
        await message.reply_text("Invalid structure format. Supply a valid Kenyan mobile line number:")
        return AWAITING_PHONE

    await message.reply_text(f"🔄 Dispatched an M-Pesa push prompt token to screen {mask_msisdn(phone)}. Complete authorization by keying in your PIN code.")
    try:
        amount_lookup = {
            "bun_1w": 499, "bun_1m": 1299, "bun_3m": 1999, "bun_lt": 2499,
            "ch1_1w": 199, "ch1_1m": 499, "ch1_3m": 999, "ch1_lt": 1299,
            "ch2_1w": 399, "ch2_1m": 699, "ch2_3m": 999, "ch2_lt": 1499,
            "ch3_1w": 399, "ch3_1m": 699, "ch3_3m": 1299, "ch3_lt": 1999,
            "ch4_1w": 199, "ch4_1m": 499, "ch4_3m": 999, "ch4_lt": 1499,
        }
        amount = amount_lookup.get(plan_code, 699)
        resp = await mpesa_client.trigger_stk_push(phone=phone, amount=amount, account_ref=f"SUB{user.id}")
        async with session_scope() as session:
            await create_pending_payment(session=session, telegram_id=user.id, channel_id=channel_id, plan_code=plan_code, phone=phone, checkout_id=resp["CheckoutRequestID"])
    except MpesaError as exc:
        await message.reply_text(f"Could not initialize mobile push: {exc}. Run /start to retry.")
        return ConversationHandler.END
    except Exception:
        logger.exception("STK push connection failure")
        await message.reply_text("Something went wrong while contacting Safaricom. Try again shortly.")
        return ConversationHandler.END

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("Cancelled. Send /start when you are ready to subscribe.")
    return ConversationHandler.END


def build_application() -> Application:
    settings = get_settings()
    application = Application.builder().token(settings.telegram_bot_token).build()
    
    conversation = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECTING_CHANNEL: [
                CallbackQueryHandler(channel_selected, pattern=r"^chan:"),
                CallbackQueryHandler(channel_selected, pattern=r"^nav_account")
            ],
            SELECTING_PLAN: [
                CallbackQueryHandler(plan_selected, pattern=r"^plan:"),
                CallbackQueryHandler(plan_selected, pattern=r"^nav_home")
            ],
                        # 🌟 FIXED STATE: If they send a command like /start, it escapes the phone entry window instantly
            AWAITING_PHONE: [
                CommandHandler("start", start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_phone)
            ],

        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )
    
    application.add_handler(conversation)
    return application


async def fulfill_successful_payment(telegram_id: int, channel_id: int, plan_code: str, expires_at: datetime) -> None:
    settings = get_settings()
    expiry_str = _format_expiry(expires_at)
    
    async with Bot(token=settings.telegram_bot_token) as bot_client:
        try:
            if channel_id == 999999:
                success_msg = f"👑 **ALL-ACCESS VIP BUNDLE PURCHASE CONFIRMED!** 🎉\n\nYour master membership is active. Use your private single-use invite link below to enter the channel community:\n\n"
                link_obj = await bot_client.create_chat_invite_link(chat_id=-1004338264708, member_limit=1, expire_date=datetime.now(timezone.utc) + timedelta(hours=settings.invite_link_ttl_hours))
                success_msg += f"🔗 {link_obj.invite_link}\n\n⏰ **All Access Period Expires:** `{expiry_str}`"
                await bot_client.send_message(chat_id=telegram_id, text=success_msg, parse_mode=ParseMode.MARKDOWN)
            else:
                invite_link_obj = await bot_client.create_chat_invite_link(chat_id=-1004338264708, member_limit=1, expire_date=datetime.now(timezone.utc) + timedelta(hours=settings.invite_link_ttl_hours))
                await bot_client.send_message(
                    chat_id=telegram_id,
                    text=f"✅ *Payment Confirmed!*\n\nYour subscription for plan code *{plan_code.upper()}* is now active.\n\n🔗 *Single-use Join Link:* {invite_link_obj.invite_link}\n⏰ *Access Expires:* `{expiry_str}`\n\n_Note: Links are strictly single-use and valid for 24 hours._",
                    parse_mode=ParseMode.MARKDOWN
                )
        except TelegramError as e:
            logger.error(f"Invite link generation error context: {e}")


async def notify_failed_payment(telegram_id: int, plan_code: str, reason: str) -> None:
    settings = get_settings()
    async with Bot(token=settings.telegram_bot_token) as bot_client:
        try:
            await bot_client.send_message(chat_id=telegram_id, text=f"❌ *M-Pesa Payment Failed*\n\nYour transaction setup for *{plan_code.upper()}* was cancelled.\n⚠️ *Reason:* `{reason}`\n\nRun /start to retry.", parse_mode=ParseMode.MARKDOWN)
        except TelegramError:
            pass
