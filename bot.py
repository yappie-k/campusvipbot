"""Telegram bot: channel menu, plan selection, M-Pesa checkout, account view and support.

The handlers are stateless on purpose. Menu navigation lives in the button callback data,
so /start works at any time and a bot restart (Render free tier sleeps) never leaves a
user stuck. The only per-user memory is a short-lived "waiting for phone number" flag.
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.helpers import escape_markdown

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

EAT = timezone(timedelta(hours=3))
WELCOME_BANNER_URL = "https://i.ibb.co/ZRkZwSXC/photo-5817979424224251619-x.jpg"

# Menu products. "bun" is the All-Access bundle (its button callback stays "chan:999999").
PRODUCT_LABELS = {
    "ch1": "📺❤️ SOMALI PORN❤️",
    "ch2": "❤️ EXPOSEE💦📺",
    "ch3": "📺 TRENDING LEAKS🔞💦",
    "ch4": "📺🔞 KENYAN PORN⛔",
    "bun": "💎 ALL OF THE ABOVE❤️💎",
}
PRICES = {
    "ch1": {"1w": 199, "1m": 499, "3m": 999, "lt": 1299},
    "ch2": {"1w": 399, "1m": 699, "3m": 999, "lt": 1499},
    "ch3": {"1w": 399, "1m": 699, "3m": 1299, "lt": 1999},
    "ch4": {"1w": 199, "1m": 499, "3m": 999, "lt": 1499},
    "bun": {"1w": 499, "1m": 1299, "3m": 1999, "lt": 2499},
}
DURATION_LABELS = {
    "1w": "📅 1 Week — 7 days",
    "1m": "📅 1 MONTH — 30 days",
    "3m": "📅 3 MONTHS — 90 days",
    "lt": "📅 LIFETIME",
}
DURATION_SHORT = {"1w": "1 Week", "1m": "1 Month", "3m": "3 Months", "lt": "Lifetime"}

_stk_locks: dict[int, asyncio.Lock] = {}


def _lock_for(telegram_id: int) -> asyncio.Lock:
    if telegram_id not in _stk_locks:
        _stk_locks[telegram_id] = asyncio.Lock()
    return _stk_locks[telegram_id]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_expiry(when: datetime) -> str:
    when_utc = ensure_utc(when)
    if when_utc.year >= 2100:
        return "Never (Lifetime)"
    return when_utc.astimezone(EAT).strftime("%d-%b-%Y %H:%M EAT")


def _split_plan_code(plan_code: str) -> tuple[str, str]:
    product, _, duration = str(plan_code).partition("_")
    return product, duration


def _price_for(plan_code: str) -> int | None:
    """PLANS_JSON is the source of truth; the PRICES table is only a fallback."""
    plan = get_settings().plan_by_code(plan_code)
    if plan:
        return int(plan.amount)
    product, duration = _split_plan_code(plan_code)
    return PRICES.get(product, {}).get(duration)


def _describe_plan(plan_code: str) -> str:
    product, duration = _split_plan_code(plan_code)
    label = PRODUCT_LABELS.get(product)
    if label is None:
        return str(plan_code).upper()
    return f"{label} — {DURATION_SHORT.get(duration, duration)}"


def _support_url() -> str:
    raw = get_settings().support_admin_username.strip()
    if raw.startswith("http"):
        return raw
    return f"https://t.me/{raw.lstrip('@/')}"


def main_menu_markup() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(PRODUCT_LABELS[p], callback_data=f"chan:{'999999' if p == 'bun' else p}")]
        for p in ("ch1", "ch2", "ch3", "ch4", "bun")
    ]
    rows.append(
        [
            InlineKeyboardButton("👤 My Account", callback_data="nav_account"),
            InlineKeyboardButton("💬 Help Support", url=_support_url()),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _back_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_home")]])


def _welcome_caption(first_name: str | None) -> str:
    name = escape_markdown(first_name or "Subscriber", version=1)
    return (
        f"Hello {name}\n"
        "🔥 *WELCOME TO SABLENYAVIPs  MASTER ACCESS PORTAL* ❤️\n\n"
        "Select Premium Channel or choose the *ALL OF THE ABOVE* to instantly unlock all communities at a massive discount! 👇"
    )


async def _edit(query, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """Edit the menu message in place. The welcome message is a photo, so it has a caption, not text."""
    try:
        if getattr(query.message, "photo", None):
            await query.edit_message_caption(caption=text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        else:
            await query.edit_message_text(text=text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    context.user_data.clear()  # /start always resets any half-finished checkout

    try:
        async with session_scope() as session:
            await upsert_user(session, user.id, user.username, user.first_name)
    except Exception:
        logger.exception("Could not save user %s; showing menu anyway", user.id)

    caption = _welcome_caption(user.first_name)
    try:
        await message.reply_photo(
            photo=WELCOME_BANNER_URL,
            caption=caption,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_markup(),
        )
    except TelegramError:
        logger.exception("Welcome banner failed; falling back to text menu")
        await message.reply_text(caption, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_markup())


async def on_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    context.user_data.clear()
    await _edit(query, _welcome_caption(query.from_user.first_name), main_menu_markup())


async def on_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    async with session_scope() as session:
        active_subs = await get_all_user_subscriptions(session, query.from_user.id)

    text = "👤 *YOUR MEMBERSHIP ACCOUNT*\n\n"
    if not active_subs:
        text += "❌ You are not subscribed to any channel at the moment.\n\nTap *Back* and pick a channel to subscribe."
    else:
        text += "✅ *Channels you are subscribed to:*\n\n"
        for sub in active_subs:
            text += (
                f"▪️ {escape_markdown(_describe_plan(sub.plan_code), version=1)}\n"
                f"   ⏱️ Expires: `{_format_expiry(sub.expires_at)}`\n\n"
            )
    await _edit(query, text, _back_markup())


async def on_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    key = query.data.split(":", 1)[1]
    product = "bun" if key == "999999" else key
    if product not in PRICES:
        await _edit(query, _welcome_caption(query.from_user.first_name), main_menu_markup())
        return

    rows = []
    for d in PRICES[product]:
        price = _price_for(f"{product}_{d}")
        rows.append(
            [InlineKeyboardButton(f"{DURATION_LABELS[d]} | {price} KSHS", callback_data=f"plan:{product}_{d}:{price}")]
        )
    rows.append([InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="nav_home")])

    await _edit(
        query,
        f"🛒 *{escape_markdown(PRODUCT_LABELS[product], version=1)}*\n\nChoose how long you want access 👇",
        InlineKeyboardMarkup(rows),
    )


async def on_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    plan_code = query.data.split(":")[1]
    price = _price_for(plan_code)  # price always comes from the server-side table
    if price is None:
        await _edit(query, _welcome_caption(query.from_user.first_name), main_menu_markup())
        return

    context.user_data["plan_code"] = plan_code
    context.user_data["billing_amount"] = price
    context.user_data["awaiting_phone"] = True

    hint = ""
    try:
        async with session_scope() as session:
            db_user = await upsert_user(
                session, query.from_user.id, query.from_user.username, query.from_user.first_name
            )
            if db_user.phone:
                hint = f"\n\nLast number on file: `{mask_msisdn(db_user.phone)}`"
    except Exception:
        logger.exception("Could not load saved phone for %s", query.from_user.id)

    await _edit(
        query,
        f"Selected: *{escape_markdown(_describe_plan(plan_code), version=1)}*\n"
        f"Amount: *{price} KSHS*\n\n"
        "📱 Send your *M-Pesa phone number* now and an M-Pesa prompt will pop up on your phone.\n"
        "Examples: `0712345678` or `254712345678`."
        f"{hint}\n\nSend /cancel to stop.",
        _back_markup(),
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.text is None:
        return
    if context.user_data.get("awaiting_phone"):
        await receive_phone(update, context)
    else:
        await message.reply_text("Send /start to open the menu.")


async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or message.text is None:
        return

    plan_code = context.user_data.get("plan_code")
    amount = context.user_data.get("billing_amount")
    if not plan_code or amount is None:
        context.user_data.clear()
        await message.reply_text("Your session expired. Please send /start and pick a channel again.")
        return

    phone = normalize_msisdn(message.text)
    if phone is None:
        await message.reply_text("That doesn't look like a valid Kenyan number. Try again, e.g. 0712345678:")
        return

    lock = _lock_for(user.id)
    if lock.locked():
        await message.reply_text("⏳ Still processing your previous request, please wait a moment.")
        return

    settings = get_settings()
    async with lock:
        try:
            resp = await mpesa_client.trigger_stk_push(phone=phone, amount=int(amount), account_ref=f"SUB{user.id}")
            async with session_scope() as session:
                await create_pending_payment(
                    session=session,
                    telegram_id=user.id,
                    channel_id=settings.main_channel_id,
                    plan_code=str(plan_code),
                    phone=phone,
                    checkout_id=resp["CheckoutRequestID"],
                )
        except MpesaError as exc:
            await message.reply_text(
                f"Could not start the M-Pesa prompt: {exc}\n\nSend your number again to retry, or /start to go back."
            )
            return
        except Exception:
            logger.exception("STK push failure")
            await message.reply_text(
                "Something went wrong while contacting Safaricom. Send your number again to retry, or /start to go back."
            )
            return

    context.user_data.clear()
    await message.reply_text(
        f"🔄 M-Pesa prompt sent to {mask_msisdn(phone)}. Enter your PIN to pay.\n"
        "Your channel join link will arrive here automatically once payment is confirmed."
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    if update.effective_message:
        await update.effective_message.reply_text("Cancelled. Send /start when you are ready to subscribe.")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled error while processing an update", exc_info=context.error)


def build_application() -> Application:
    settings = get_settings()
    application = Application.builder().token(settings.telegram_bot_token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(on_home, pattern=r"^nav_home$"))
    application.add_handler(CallbackQueryHandler(on_account, pattern=r"^nav_account$"))
    application.add_handler(CallbackQueryHandler(on_channel, pattern=r"^chan:"))
    application.add_handler(CallbackQueryHandler(on_plan, pattern=r"^plan:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_error_handler(on_error)
    return application


# ---------------------------------------------------------------------------
# Called from the payment webhook
# ---------------------------------------------------------------------------

async def fulfill_successful_payment(telegram_id: int, channel_id: int, plan_code: str, expires_at: datetime) -> None:
    settings = get_settings()
    target_chat = settings.main_channel_id if channel_id == 999999 else channel_id
    plan_text = html.escape(_describe_plan(plan_code))

    try:
        async with Bot(token=settings.telegram_bot_token) as bot_client:
            link = await bot_client.create_chat_invite_link(
                chat_id=target_chat,
                member_limit=1,
                expire_date=datetime.now(timezone.utc) + timedelta(hours=settings.invite_link_ttl_hours),
            )
            await bot_client.send_message(
                chat_id=telegram_id,
                text=(
                    f"✅ <b>Payment confirmed!</b>\n\n"
                    f"{plan_text} is now active.\n\n"
                    f"🔗 <b>Your join link</b> (single use, valid {settings.invite_link_ttl_hours}h):\n"
                    f"{html.escape(link.invite_link)}\n\n"
                    f"⏰ <b>Access expires:</b> <code>{_format_expiry(expires_at)}</code>"
                ),
                parse_mode=ParseMode.HTML,
            )
    except Exception:
        logger.exception("Could not deliver invite link to user %s", telegram_id)
        try:
            async with Bot(token=settings.telegram_bot_token) as bot_client:
                await bot_client.send_message(
                    chat_id=telegram_id,
                    text="✅ Payment confirmed, but we couldn't generate your join link automatically. "
                    "Please tap Help Support in /start and we'll add you right away.",
                )
        except TelegramError:
            logger.exception("Fallback message to user %s also failed", telegram_id)


async def notify_failed_payment(telegram_id: int, plan_code: str, reason: str) -> None:
    settings = get_settings()
    try:
        async with Bot(token=settings.telegram_bot_token) as bot_client:
            await bot_client.send_message(
                chat_id=telegram_id,
                text=(
                    "❌ <b>M-Pesa payment failed</b>\n\n"
                    f"{html.escape(_describe_plan(plan_code))} was not completed.\n"
                    f"⚠️ <b>Reason:</b> <code>{html.escape(str(reason))}</code>\n\n"
                    "Send /start to try again."
                ),
                parse_mode=ParseMode.HTML,
            )
    except TelegramError:
        logger.exception("Could not notify user %s of failed payment", telegram_id)
