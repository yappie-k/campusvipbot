"""
database.py - SQLAlchemy Async Database Models and Data Access Layer

Optimized for PostgreSQL (asyncpg on Render) with graceful SQLite fallback.
Tracks MegaPay transactions by reference and transaction ID.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, List
from urllib.parse import urlparse, urlunparse
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    Numeric,
    Index,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

from config import DATABASE_URL, IS_POSTGRES, logger

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    telegram_id = Column(BigInteger, primary_key=True, index=True)
    username = Column(String(100), nullable=True)
    first_name = Column(String(150), nullable=True)
    phone_number = Column(String(20), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    reference = Column(String(100), unique=True, index=True, nullable=False)
    transaction_id = Column(String(100), index=True, nullable=True)
    telegram_id = Column(BigInteger, index=True, nullable=False)
    plan_code = Column(String(50), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    phone_number = Column(String(20), nullable=False)
    status = Column(String(20), default="PENDING", index=True)
    receipt_number = Column(String(50), nullable=True, index=True)
    response_code = Column(Integer, nullable=True)
    response_desc = Column(Text, nullable=True)
    raw_callback = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, index=True, nullable=False)
    plan_code = Column(String(50), nullable=False)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False, index=True)
    status = Column(String(20), default="ACTIVE", index=True)
    invite_link = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_subscriptions_status_end_date", "status", "end_date"),
    )


engine_kwargs = {"echo": False, "future": True}
clean_db_url = DATABASE_URL
connect_args = {}

if IS_POSTGRES:
    if "sslmode=" in clean_db_url:
        parsed = urlparse(clean_db_url)
        clean_db_url = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            "",
            parsed.fragment,
        ))
        connect_args["ssl"] = "require"
    elif "render.com" in clean_db_url:
        connect_args["ssl"] = "require"

    if connect_args:
        engine_kwargs["connect_args"] = connect_args

engine = create_async_engine(clean_db_url, **engine_kwargs)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema initialized successfully.")


async def upsert_user(
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    phone_number: Optional[str] = None,
) -> User:
    async with async_session_factory() as session:
        async with session.begin():
            result = await session.execute(
                select(User).where(User.telegram_id == telegram_id)
            )
            user = result.scalar_one_or_none()

            if user is None:
                user = User(
                    telegram_id=telegram_id,
                    username=username,
                    first_name=first_name,
                    phone_number=phone_number,
                )
                session.add(user)
            else:
                if username is not None:
                    user.username = username
                if first_name is not None:
                    user.first_name = first_name
                if phone_number is not None:
                    user.phone_number = phone_number
                user.updated_at = datetime.now(timezone.utc)

            await session.flush()
            await session.refresh(user)
            return user


async def save_payment_initiated(
    reference: str,
    telegram_id: int,
    plan_code: str,
    amount: float,
    phone_number: str,
    transaction_id: Optional[str] = None,
) -> PaymentTransaction:
    async with async_session_factory() as session:
        async with session.begin():
            tx = PaymentTransaction(
                reference=reference,
                transaction_id=transaction_id,
                telegram_id=telegram_id,
                plan_code=plan_code,
                amount=amount,
                phone_number=phone_number,
                status="PENDING",
            )
            session.add(tx)
            await session.flush()
            await session.refresh(tx)
            return tx


async def get_transaction_by_ref_or_id(
    reference: Optional[str] = None,
    transaction_id: Optional[str] = None,
) -> Optional[PaymentTransaction]:
    async with async_session_factory() as session:
        conditions = []
        if reference:
            conditions.append(PaymentTransaction.reference == reference)
        if transaction_id:
            conditions.append(PaymentTransaction.transaction_id == transaction_id)
            conditions.append(PaymentTransaction.reference == transaction_id)

        if not conditions:
            return None

        result = await session.execute(
            select(PaymentTransaction).where(or_(*conditions))
        )
        return result.scalars().first()


async def mark_payment_result(
    tx_id_or_ref: str,
    status: str,
    response_code: int,
    response_desc: str,
    receipt_number: Optional[str] = None,
    megapay_tx_id: Optional[str] = None,
    raw_callback: Optional[str] = None,
) -> Optional[PaymentTransaction]:
    async with async_session_factory() as session:
        async with session.begin():
            result = await session.execute(
                select(PaymentTransaction).where(
                    or_(
                        PaymentTransaction.reference == tx_id_or_ref,
                        PaymentTransaction.transaction_id == tx_id_or_ref,
                    )
                )
            )
            tx = result.scalars().first()
            if not tx:
                return None

            tx.status = status
            tx.response_code = response_code
            tx.response_desc = response_desc
            if receipt_number:
                tx.receipt_number = receipt_number
            if megapay_tx_id:
                tx.transaction_id = megapay_tx_id
            if raw_callback:
                tx.raw_callback = raw_callback
            tx.updated_at = datetime.now(timezone.utc)

            await session.flush()
            await session.refresh(tx)
            return tx


async def get_active_subscription(telegram_id: int) -> Optional[Subscription]:
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        result = await session.execute(
            select(Subscription)
            .where(
                Subscription.telegram_id == telegram_id,
                Subscription.status == "ACTIVE",
                Subscription.end_date > now,
            )
            .order_by(Subscription.end_date.desc())
        )
        return result.scalars().first()


async def activate_or_extend_subscription(
    telegram_id: int,
    plan_code: str,
    duration_days: int,
    invite_link: Optional[str] = None,
) -> Subscription:
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        async with session.begin():
            result = await session.execute(
                select(Subscription)
                .where(
                    Subscription.telegram_id == telegram_id,
                    Subscription.status == "ACTIVE",
                    Subscription.end_date > now,
                )
                .order_by(Subscription.end_date.desc())
            )
            current_sub = result.scalars().first()

            is_lifetime = duration_days >= 9999
            add_days = 36500 if is_lifetime else duration_days

            if current_sub:
                new_end = current_sub.end_date + timedelta(days=add_days)
                current_sub.end_date = new_end
                current_sub.plan_code = plan_code
                if invite_link:
                    current_sub.invite_link = invite_link
                current_sub.updated_at = now
                await session.flush()
                await session.refresh(current_sub)
                return current_sub
            else:
                end_date = now + timedelta(days=add_days)
                new_sub = Subscription(
                    telegram_id=telegram_id,
                    plan_code=plan_code,
                    start_date=now,
                    end_date=end_date,
                    status="ACTIVE",
                    invite_link=invite_link,
                )
                session.add(new_sub)
                await session.flush()
                await session.refresh(new_sub)
                return new_sub


async def get_expired_active_subscriptions() -> List[Subscription]:
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        result = await session.execute(
            select(Subscription).where(
                Subscription.status == "ACTIVE",
                Subscription.end_date <= now,
            )
        )
        return list(result.scalars().all())


async def mark_subscription_expired(subscription_id: int) -> None:
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        async with session.begin():
            await session.execute(
                update(Subscription)
                .where(Subscription.id == subscription_id)
                .values(status="EXPIRED", updated_at=now)
            )
            logger.info("Marked subscription #%d as EXPIRED in database.", subscription_id)
