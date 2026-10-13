"""
bot.py - Telegram Subscription Bot (python-telegram-bot v20+ Async)

Handles:
1. /start command: user greeting, plan presentation via inline keyboard.
2. /status command: displays current active subscription, expiry countdown in Africa/Nairobi time.
3. Plan selection callback queries.
4. Phone number input, normalization, and MegaPay STK Push dispatching.
5. Single-use invite link generation (member_limit=1, TTL=24h).
"""

import time
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHANNEL_ID,
    TELEGRAM_CHANNEL_TITLE,
    BOT_PUBLIC_USERNAME,
    SUBSCRIPTION_PLANS,
    INVITE_LINK_TTL_HOURS,
    APP_TIMEZONE,
    get_plan_by_code,
    logger,
)
from database import (
    upsert_user,
    get_active_subscription,
    save_payment_initiated,
)
from megapay import (
    sanitize_phone_number,
    trigger_megapay_stk_push,
)


def build_plans_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for plan in SUBSCRIPTION_PLANS:
        label = plan.get("name") or f"💎 {plan.get('code')} — KES {plan.get('amount')}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"plan:{plan.get('code')}")])
    return InlineKeyboardMarkup(buttons)


def build_renewal_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for plan in SUBSCRIPTION_PLANS:
        label = f"⚡ Renew: {plan.get('name')}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"plan:{plan.get('code')}")])
    return InlineKeyboardMarkup(buttons)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    await upsert_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    active_sub = await get_active_subscription(user.id)
    if active_sub:
        time_left = active_sub.end_date - datetime.now(timezone.utc)
        days = max(0, time_left.days)
        hours = max(0, time_left.seconds // 3600)
        local_expiry = active_sub.end_date.astimezone(APP_TIMEZONE).strftime("%d %b %Y, %I:%M %p EAT")

        msg = (
            f"👋 Hello **{user.first_name}**!\n\n"
            f"✅ **You already have an ACTIVE VIP Subscription!**\n\n"
            f"📢 Channel: **{TELEGRAM_CHANNEL_TITLE}**\n"
            f"⏳ Valid until: `{local_expiry}`\n"
            f"⏱️ Time remaining: **{days} days, {hours} hours**\n\n"
        )
        if active_sub.invite_link:
            msg += f"🔗 Your Invite Link: {active_sub.invite_link}\n\n"

        msg += "Need to extend or upgrade your access? Select a plan below:"

        await update.message.reply_text(
            text=msg,
            reply_markup=build_plans_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    welcome_text = (
        f"👋 Welcome **{user.first_name}** to **{TELEGRAM_CHANNEL_TITLE}**!\n\n"
        "join our private VIP Telegram channel.\n\n"
        "💳 **Instant M-Pesa Activation:**\n"
        "1. Select your subscription duration below.\n"
        "2. Enter your Safaricom M-Pesa phone number.\n"
        "3. Enter your M-Pesa PIN on your phone screen.\n"
        "4. Your single-use private channel link arrives immediately!\n\n"
        "👇 **Choose a subscription plan below:**"
    )

    await update.message.reply_text(
        text=welcome_text,
        reply_markup=build_plans_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    active_sub = await get_active_subscription(user.id)
    if not active_sub:
        await update.message.reply_text(
            "❌ You do not currently have an active VIP subscription.\n\n"
            "Use /start to choose a plan and join the VIP channel!",
            reply_markup=build_plans_keyboard(),
        )
        return

    time_left = active_sub.end_date - datetime.now(timezone.utc)
    days = max(0, time_left.days)
    hours = max(0, time_left.seconds // 3600)
    local_expiry = active_sub.end_date.astimezone(APP_TIMEZONE).strftime("%d %b %Y, %I:%M %p EAT")

    text = (
        f"📋 **VIP Membership Status**\n\n"
        f"👤 Member: **{user.first_name}** (`{user.id}`)\n"
        f"📢 Channel: **{TELEGRAM_CHANNEL_TITLE}**\n"
        f"🏷️ Plan: **{active_sub.plan_code.upper()}**\n"
        f"📅 Expiry Date: `{local_expiry}`\n"
        f"⏳ Time Remaining: **{days}d {hours}h**\n"
        f"🟢 Status: **ACTIVE**\n"
    )
    if active_sub.invite_link:
        text += f"\n🔗 Invite Link: {active_sub.invite_link}"

    await update.message.reply_text(text=text, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "ℹ️ **Bot Navigation & Commands**\n\n"
        "• `/start` — View membership plans and subscribe\n"
        "• `/status` — Check active membership validity & expiry\n"
        "• `/help` — Show help and support instructions\n\n"
        "💡 **How Lipa Na M-Pesa STK Push Works:**\n"
        "Enter your phone number (e.g., `0712345678` or `+254712345678`). A payment prompt will appear automatically on your phone screen asking for your M-Pesa PIN. As soon as you authorize, the bot generates your single-use invite link.\n\n"
        f"Support: @{BOT_PUBLIC_USERNAME}"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not query.data or not query.data.startswith("plan:"):
        return

    plan_code = query.data.split("plan:")[1]
    plan = get_plan_by_code(plan_code)
    if not plan:
        await query.edit_message_text("❌ Selected plan not found. Please try /start again.")
        return

    context.user_data["pending_plan_code"] = plan_code

    prompt_text = (
        f"💎 **Selected Plan:** {plan.get('name')}\n"
        f"💰 **Amount to Pay:** KES {plan.get('amount'):,}\n\n"
        "📱 **Please enter your M-Pesa phone number** below to receive STK Push prompt on your handset:\n\n"
        "Accepted formats:\n"
        "• `0712345678`\n"
        "• `0112345678`\n"
        "• `+254712345678`\n"
        "• `254712345678`\n\n"
        "*(Reply directly with your phone number now)*"
    )

    await query.edit_message_text(
        text=prompt_text,
        parse_mode=ParseMode.MARKDOWN,
    )


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    if not user:
        return

    user_text = update.message.text.strip()
    pending_plan_code = context.user_data.get("pending_plan_code")

    if not pending_plan_code:
        await update.message.reply_text(
            "Please use /start to select a subscription plan first.",
            reply_markup=build_plans_keyboard(),
        )
        return

    plan = get_plan_by_code(pending_plan_code)
    if not plan:
        context.user_data.pop("pending_plan_code", None)
        await update.message.reply_text("Invalid plan selection. Please restart with /start.")
        return

    try:
        sanitized_phone = sanitize_phone_number(user_text)
    except ValueError as val_err:
        await update.message.reply_text(
            f"⚠️ {str(val_err)}\n\n"
            "Please enter a valid Kenyan Safaricom/Airtel number (e.g., `0712345678`).",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await upsert_user(telegram_id=user.id, phone_number=sanitized_phone)

    status_msg = await update.message.reply_text(
        f"🔄 Contacting MegaPay Gateway...\n"
        f"Sending STK Push prompt of **KES {plan['amount']:,}** to **{sanitized_phone}**...",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        unique_ref = f"TG_{user.id}_{int(time.time())}"

        stk_response = await trigger_megapay_stk_push(
            phone_number=sanitized_phone,
            amount=plan["amount"],
            reference=unique_ref,
        )

        megapay_tx_id = stk_response.get("TransactionID") or stk_response.get("transaction_id") or ""

        await save_payment_initiated(
            reference=unique_ref,
            telegram_id=user.id,
            plan_code=plan["code"],
            amount=float(plan["amount"]),
            phone_number=sanitized_phone,
            transaction_id=megapay_tx_id,
        )

        context.user_data.pop("pending_plan_code", None)

        confirmation_text = (
            "📲 **M-Pesa STK Prompt Sent!**\n\n"
            f"A payment prompt has popped up on your phone (`{sanitized_phone}`).\n\n"
            f"1. Unlock your phone screen.\n"
            f"2. Enter your **M-Pesa PIN** to authorize KES {plan['amount']:,}.\n"
            f"3. Wait a few seconds for automatic confirmation.\n\n"
            "✨ *Once payment is received, your single-use invite link will be issued here automatically!*"
        )

        await status_msg.edit_text(confirmation_text, parse_mode=ParseMode.MARKDOWN)

    except Exception as exc:
        logger.error("Failed to trigger MegaPay STK push for user %d: %s", user.id, exc)
        await status_msg.edit_text(
            f"❌ **Failed to initiate MegaPay STK Push:**\n`{str(exc)}`\n\n"
            "Please check that your phone number is correct and try again with /start.",
            parse_mode=ParseMode.MARKDOWN,
        )


async def generate_single_use_invite_link(bot: Application.bot, user_id: int) -> str:
    try:
        expire_timestamp = datetime.now(timezone.utc) + timedelta(hours=INVITE_LINK_TTL_HOURS)
        invite = await bot.create_chat_invite_link(
            chat_id=TELEGRAM_CHANNEL_ID,
            member_limit=1,
            expire_date=expire_timestamp,
            name=f"VIP-{user_id}",
        )
        logger.info("Generated single-use invite link for user %d: %s", user_id, invite.invite_link)
        return invite.invite_link
    except Exception as exc:
        logger.error("Failed to create chat invite link for channel %d: %s", TELEGRAM_CHANNEL_ID, exc)
        raise RuntimeError(
            f"Failed to generate Telegram invite link. Please verify bot is admin in channel {TELEGRAM_CHANNEL_ID} with 'Invite Users via Link' permission."
        ) from exc


def create_telegram_bot_app() -> Application:
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not configured.")

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("myplan", status_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(plan_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    return application
