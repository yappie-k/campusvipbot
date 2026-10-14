"""System settings parser handling nested multi-channel data dictionaries."""
from __future__ import annotations
import os
import json
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Plan:
    code: str
    name: str
    days: int
    amount: int
    channel_id: int

class Settings:
    def __init__(self) -> None:
        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.bot_public_username: str = os.getenv("BOT_PUBLIC_USERNAME", "")
        self.database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bot.db")
        self.http_host: str = os.getenv("HTTP_HOST", "0.0.0.0")
        self.http_port: int = int(os.getenv("HTTP_PORT", 10000))
        self.callback_base_url: str = os.getenv("CALLBACK_BASE_URL", "").rstrip("/")
        self.support_admin_username: str = os.getenv("SUPPORT_ADMIN_USERNAME", "CampusVIP_Admin")
        
        self.megapay_api_key: str = os.getenv("MEGAPAY_API_KEY", "")
        self.megapay_email: str = os.getenv("MEGAPAY_EMAIL", "")
        self.mpesa_callback_path: str = os.getenv("MPESA_CALLBACK_PATH", "/pay/cb")
        
        self.expiry_check_interval_minutes: int = int(os.getenv("EXPIRY_CHECK_INTERVAL_MINUTES", 5))
        self.invite_link_ttl_hours: int = int(os.getenv("INVITE_LINK_TTL_HOURS", 24))
        self.stk_reconcile_after_seconds: int = int(os.getenv("STK_RECONCILE_AFTER_SECONDS", 120))

        # 🌟 Maps multiple channel identifiers and pricing keys cleanly into memory lookups
        self.channels: dict[int, str] = {}
        self.plans: dict[str, Plan] = {}
        
        try:
            raw_json = os.getenv("PLANS_JSON", "[]")
            parsed_data = json.loads(raw_json)
            for ch in parsed_data:
                c_id = int(ch["channel_id"])
                self.channels[c_id] = ch["channel_name"]
                for p in ch["plans"]:
                    self.plans[p["code"]] = Plan(
                        code=p["code"],
                        name=p["name"],
                        days=p["days"],
                        amount=p["amount"],
                        channel_id=c_id
                    )
        except Exception:
            pass

    def plan_by_code(self, code: str) -> Plan | None:
        return self.plans.get(code)

_settings = Settings()
def get_settings() -> Settings:
    return _settings
