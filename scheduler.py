"""
scheduler.py - APScheduler Auto-Expulsion & Expiration Engine

Runs an AsyncIOScheduler background job that:
1. Periodically checks database for subscriptions where end_date <= NOW() and status == 'ACTIVE'.
2. Kicks the member from the Telegram Channel using the standard Telegram kick idiom:
   a. ban_chat_member(chat_id, user_id) -> kicks user immediately
   b. unban_chat_member(chat_id, user_id) -> clears banlist so they can re-join later
3. Updates subscription record to 'EXPIRED'.
4. Direct messages the user notifying them of expiration with one-click renewal buttons.
"""

from datetime import timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from config import (
    TELEGRAM_CHANNEL_ID,
    TELEGRAM_CHANNEL_TITLE,
    EXPIRATION_CHECK_INTERVAL_SECONDS,
    logger,
)
from database import (
    get_expired_active_subscriptions,
    mark_subscription_expired,
)
from bot import build_renewal_keyboard


async def check_and_expel_expired_members(bot: Bot) -> int:
    logger.debug("Running auto-expulsion scan for expired subscriptions...")
    expired_list = await get_expired_active_subscriptions()

    if not expired_list:
        logger.debug("No expired subscriptions found in this cycle.")
        return 0

    logger.info("Found %d expired subscriptions to process for removal.", len(expired_list))
    expelled_count = 0

    for sub in expired_list:
        user_id = sub.telegram_id
        sub_id = sub.id

        try:
            logger.info("Kicking expired user %d from channel %d...", user_id, TELEGRAM_CHANNEL_ID)
            await bot.ban_chat_member(
                chat_id=TELEGRAM_CHANNEL_ID,
                user_id=user_id,
            )

            await bot.unban_chat_member(
                chat_id=TELEGRAM_CHANNEL_ID,
                user_id=user_id,
            )
            logger.info("Successfully kicked and unbanned user %d from channel.", user_id)

        except TelegramError as tg_err:
            logger.warning(
                "Telegram API error when kicking user %d: %s. Proceeding with database status update.",
                user_id,
                tg_err,
            )
        except Exception as e:
            logger.error("Unexpected error kicking user %d: %s", user_id, e)

        try:
            await mark_subscription_expired(sub_id)
        except Exception as db_err:
            logger.error("Database update error for sub #%d: %s", sub_id, db_err)

        try:
            renewal_msg = (
                f"⚠️ **VIP Membership Expired**\n\n"
                f"Your subscription to **{TELEGRAM_CHANNEL_TITLE}** has ended and your channel access has been revoked.\n\n"
                "To restore your access and continue receiving private updates, renew instantly below:"
            )

            await bot.send_message(
                chat_id=user_id,
                text=renewal_msg,
                reply_markup=build_renewal_keyboard(),
                parse_mode=ParseMode.MARKDOWN,
            )
            logger.info("Sent renewal notification DM to user %d", user_id)
        except TelegramError as dm_err:
            logger.warning(
                "Could not send renewal PM to user %d (user may have blocked the bot): %s",
                user_id,
                dm_err,
            )

        expelled_count += 1

    logger.info("Expulsion cycle complete. Expelled and notified %d user(s).", expelled_count)
    return expelled_count


def start_expulsion_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=timezone.utc)

    scheduler.add_job(
        check_and_expel_expired_members,
        trigger=IntervalTrigger(seconds=EXPIRATION_CHECK_INTERVAL_SECONDS),
        args=[bot],
        id="check_expired_subscriptions",
        name="Check and Expel Expired Subscriptions",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.start()
    logger.info(
        "APScheduler started: scanning for expired members every %d seconds.",
        EXPIRATION_CHECK_INTERVAL_SECONDS,
    )
    return scheduler
