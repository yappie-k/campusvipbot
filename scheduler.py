"""Background loop daemon tasks worker engine orchestration."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.future import select

from config import get_settings
from database import session_scope, Subscription, Payment

logger = logging.getLogger(__name__)
_scheduler = AsyncIOScheduler()

async def expire_subscriptions_job():
    """Identifies elapsed member access boundaries and kicks them out from the premium channel."""
    settings = get_settings()
    # DB columns are naive UTC; comparing against an aware datetime fails on Postgres (asyncpg).
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    from telegram import Bot
    from telegram.error import TelegramError

    async with session_scope() as session:
        stmt = select(Subscription).where(Subscription.status == "active", Subscription.expires_at <= now)
        result = await session.execute(stmt)
        expired_subs = result.scalars().all()
        
        for sub in expired_subs:
            try:
                async with Bot(token=settings.telegram_bot_token) as bot_client:
                    # 🌟 FIX: Evicts specifically from the unique channel ID column variable mapping row
                    await bot_client.ban_chat_member(chat_id=sub.channel_id, user_id=sub.user_id)
                    await bot_client.unban_chat_member(chat_id=sub.channel_id, user_id=sub.user_id)
                    
                    sub.status = "expired"
                    await session.commit()
                    
                    await bot_client.send_message(
                        chat_id=sub.user_id,
                        text="🚨 *Subscription Expired*\n\nYour premium group membership access plan has run out. Use /start to purchase a renewal pack!",
                        parse_mode="Markdown"
                    )
                    logger.info(f"Safely evicted expired user {sub.user_id} from channel {sub.channel_id}")
            except TelegramError as e:
                logger.error(f"Expulsion execution task fault trace: {e}")

async def sweep_pending_payments():
    settings = get_settings()
    now = datetime.now(timezone.utc)
    async with session_scope() as session:
        stmt = select(Payment).where(Payment.status == "pending")
        result = await session.execute(stmt)
        pending_records = result.scalars().all()
        for payment in pending_records:
            age = (now - payment.created_at.replace(tzinfo=timezone.utc)).total_seconds()
            if age > settings.stk_reconcile_after_seconds:
                payment.status = "failed"
                await session.commit()

def start_scheduler():
    settings = get_settings()
    _scheduler.add_job(expire_subscriptions_job, 'interval', minutes=settings.expiry_check_interval_minutes, id="expire_subscriptions", replace_existing=True)
    _scheduler.add_job(sweep_pending_payments, 'interval', minutes=10, id="sweep_pending_payments", replace_existing=True)
    _scheduler.start()
    logger.info("Background automated cron daemons active.")

def stop_scheduler():
    if _scheduler.running:
        _scheduler.shutdown()
