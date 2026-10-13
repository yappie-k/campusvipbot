"""
config.py - Centralized Configuration and Environment Settings

Configured for MegaPay Kenya STK Push, Render Hosting, PostgreSQL (asyncpg),
and automated VIP Telegram channel access.
"""

import os
import sys
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Structured logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s"
logging.basicConfig(
    level=LOG_LEVEL,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("safalenya_megapay_bot")

# Timezone (Africa/Nairobi)
APP_TIMEZONE_STR = os.getenv("TIMEZONE", "Africa/Nairobi")
try:
    APP_TIMEZONE = ZoneInfo(APP_TIMEZONE_STR)
except Exception:
    APP_TIMEZONE = ZoneInfo("UTC")

# ==========================================
# Telegram Configuration
# ==========================================
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

_raw_channel_id = os.getenv("CHANNEL_CHAT_ID", os.getenv("TELEGRAM_CHANNEL_ID", "0")).strip()
try:
    TELEGRAM_CHANNEL_ID: int = int(_raw_channel_id)
except ValueError:
    logger.error("Invalid CHANNEL_CHAT_ID '%s'. Must be a signed integer (e.g. -1004338264708).", _raw_channel_id)
    TELEGRAM_CHANNEL_ID = 0

BOT_PUBLIC_USERNAME: str = os.getenv("BOT_PUBLIC_USERNAME", "CampusVIPbot").strip().lstrip("@")
TELEGRAM_CHANNEL_TITLE: str = os.getenv("TELEGRAM_CHANNEL_TITLE", "Campus VIP Private Hub").strip()

INVITE_LINK_TTL_HOURS: int = int(os.getenv("INVITE_LINK_TTL_HOURS", "24"))

# ==========================================
# Database Configuration (PostgreSQL for Render)
# ==========================================
raw_db_url = os.getenv("DATABASE_URL", "").strip()

if not raw_db_url:
    DATABASE_URL = f"sqlite+aiosqlite:///{BASE_DIR}/bot.db"
    IS_POSTGRES = False
else:
    IS_POSTGRES = True
    if raw_db_url.startswith("postgres://"):
        DATABASE_URL = raw_db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif raw_db_url.startswith("postgresql://") and not raw_db_url.startswith("postgresql+asyncpg://"):
        DATABASE_URL = raw_db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    else:
        DATABASE_URL = raw_db_url

logger.info("Database driver configured: %s", "PostgreSQL (asyncpg)" if IS_POSTGRES else "SQLite (aiosqlite)")

# ==========================================
# MegaPay Kenya STK Push Configuration
# ==========================================
MEGAPAY_API_KEY: str = os.getenv("MEGAPAY_API_KEY", "").strip()
MEGAPAY_EMAIL: str = os.getenv("MEGAPAY_EMAIL", "").strip()
MEGAPAY_INITIATE_URL: str = os.getenv(
    "MEGAPAY_INITIATE_URL", "https://megapay.co.ke/backend/v1/initiatestk"
).strip()

# ==========================================
# Web Server & Webhook Configuration
# ==========================================
HTTP_HOST: str = os.getenv("HTTP_HOST", "0.0.0.0").strip()
SERVER_PORT: int = int(os.getenv("PORT", os.getenv("HTTP_PORT", "8000")))

CALLBACK_BASE_URL: str = os.getenv("CALLBACK_BASE_URL", "https://ngrok-free.dev").strip().rstrip("/")

MEGAPAY_CALLBACK_PATH: str = os.getenv(
    "MEGAPAY_CALLBACK_PATH", os.getenv("MPESA_CALLBACK_PATH", "/pay/cb/e3d81b49f62c7a05d891e42b3c6a5d7e")
).strip()
if not MEGAPAY_CALLBACK_PATH.startswith("/"):
    MEGAPAY_CALLBACK_PATH = "/" + MEGAPAY_CALLBACK_PATH

WEBHOOK_PUBLIC_URL: str = f"{CALLBACK_BASE_URL}{MEGAPAY_CALLBACK_PATH}"

# ==========================================
# Scheduler Settings
# ==========================================
EXPIRY_CHECK_INTERVAL_MINUTES: int = int(os.getenv("EXPIRY_CHECK_INTERVAL_MINUTES", "5"))
EXPIRATION_CHECK_INTERVAL_SECONDS: int = max(30, EXPIRY_CHECK_INTERVAL_MINUTES * 60)

# ==========================================
# Subscription Plans Configuration
# ==========================================
DEFAULT_PLANS = [
    {
        "code": "weekly",
        "name": "📅 1 Week — 7 days | 399 KSHS",
        "days": 7,
        "amount": 399,
    },
    {
        "code": "monthly",
        "name": "📅 1 MONTH — 30 days | 699 KSHS",
        "days": 30,
        "amount": 699,
    },
    {
        "code": "three_months",
        "name": "📅 3 MONTHS — 90 days | 1299 KSHS",
        "days": 90,
        "amount": 1299,
    },
    {
        "code": "lifetime",
        "name": "📅 LIFETIME | 1999 KSHS",
        "days": 99999,
        "amount": 1999,
    },
]

raw_plans_json = os.getenv("PLANS_JSON", "").strip()
if raw_plans_json:
    try:
        SUBSCRIPTION_PLANS = json.loads(raw_plans_json)
    except Exception as err:
        logger.warning("Failed to parse PLANS_JSON (%s). Falling back to default plans.", err)
        SUBSCRIPTION_PLANS = DEFAULT_PLANS
else:
    SUBSCRIPTION_PLANS = DEFAULT_PLANS


def get_plan_by_code(code: str):
    for p in SUBSCRIPTION_PLANS:
        if p.get("code") == code:
            return p
    return None
