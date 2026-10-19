"""Database models mapping user tables with specific multi-channel tracking indexes."""
from __future__ import annotations
import datetime
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, DateTime, BigInteger, select
from config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, echo=False)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger)  # 🌟 Track specific channel targets
    plan_code: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="active")
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime)

class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    channel_id: Mapped[int] = mapped_column(BigInteger)  # 🌟 Lock target channel during checkout
    plan_code: Mapped[str] = mapped_column(String(50))
    phone: Mapped[str] = mapped_column(String(20))
    checkout_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)

@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

def ensure_utc(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)

async def upsert_user(session: AsyncSession, user_id: int, username: str | None, first_name: str | None) -> User:
    stmt = select(User).where(User.id == user_id)
    res = await session.execute(stmt)
    user = res.scalar_one_or_none()
    if not user:
        user = User(id=user_id, username=username, first_name=first_name)
        session.add(user)
    else:
        user.username = username
        user.first_name = first_name
    return user

async def get_active_subscription(session: AsyncSession, user_id: int, channel_id: int) -> Subscription | None:
    now = datetime.datetime.utcnow()
    stmt = select(Subscription).where(
        Subscription.user_id == user_id,
        Subscription.channel_id == channel_id,
        Subscription.status == "active",
        Subscription.expires_at > now
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()

async def get_all_user_subscriptions(session: AsyncSession, user_id: int) -> list[Subscription]:
    """🌟 REQUIRED FOR ACCOUNT BUTTON: Queries all active user memberships across all channels."""
    now = datetime.datetime.utcnow()
    stmt = select(Subscription).where(
        Subscription.user_id == user_id,
        Subscription.status == "active",
        Subscription.expires_at > now
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())

async def create_pending_payment(session: AsyncSession, telegram_id: int, channel_id: int, plan_code: str, phone: str, checkout_id: str) -> Payment:
    payment = Payment(telegram_id=telegram_id, channel_id=channel_id, plan_code=plan_code, phone=phone, checkout_id=checkout_id, status="pending")
    session.add(payment)
    return payment

async def get_payment_by_checkout(session: AsyncSession, checkout_id: str) -> Payment | None:
    stmt = select(Payment).where(Payment.checkout_id == checkout_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()

async def activate_subscription_from_payment(session: AsyncSession, payment: Payment) -> list[Subscription]:
    """🌟 BUNDLE LOGIC UNLOCKER: If the bundle code is chosen, instantly creates 5 active subscription rows."""
    payment.status = "completed"
    
    stmt = select(User).where(User.id == payment.telegram_id)
    user_res = await session.execute(stmt)
    user = user_res.scalar_one_or_none()
    if user:
        user.phone = payment.phone

    settings = get_settings()
    days = settings.plan_days(payment.plan_code)
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(days=days)

    # Every product (single channel buttons and the bundle) maps to the one real channel.
    # 999999 is the legacy bundle marker that older pending payments may still carry.
    channel_id = settings.main_channel_id if payment.channel_id == 999999 else payment.channel_id
    sub = Subscription(
        user_id=payment.telegram_id,
        channel_id=channel_id,
        plan_code=payment.plan_code,
        status="active",
        expires_at=expires_at,
    )
    session.add(sub)
    return [sub]

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)