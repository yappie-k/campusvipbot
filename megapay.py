"""MegaPay API Integration Driver Gateway Endpoint Client."""
from __future__ import annotations

import logging
import httpx
from config import get_settings

logger = logging.getLogger(__name__)

class MpesaError(Exception):
    pass

class MegaPayClient:
    def __init__(self) -> None:
        pass

    async def trigger_stk_push(self, phone: str, amount: int, account_ref: str) -> dict:
        """Dispatches an M-Pesa STK Push request through MegaPay Gateway API."""
        settings = get_settings()
        base_url = "https://megapay.co.ke"
        headers = {"Content-Type": "application/json"}
        
        callback_url = f"{settings.callback_base_url}{settings.mpesa_callback_path}"
        
        payload = {
            "api_key": settings.megapay_api_key,
            "email": settings.megapay_email,
            "amount": amount,
            "msisdn": phone,
            "transaction_reference": account_ref,
            "callback_url": callback_url
        }
        
        async with httpx.AsyncClient() as client:
            try:
                res = await client.post(base_url, json=payload, headers=headers)
                if res.status_code != 200:
                    raise MpesaError(f"MegaPay STK trigger connection failed: {res.text}")
                
                response_data = res.json()
                if response_data.get("status") == "success":
                    return {"CheckoutRequestID": response_data.get("transaction_request_id")}
                else:
                    raise MpesaError(response_data.get("message", "Unknown MegaPay gateway failure response"))
            except Exception as e:
                raise MpesaError(f"Gateway connection drop fault: {str(e)}")

def normalize_msisdn(phone: str) -> str | None:
    cleaned = "".join(filter(str.isdigit, phone))
    if cleaned.startswith("0") and len(cleaned) == 10:
        return f"254{cleaned[1:]}"
    if cleaned.startswith("254") and len(cleaned) == 12:
        return cleaned
    if len(cleaned) == 9 and cleaned.startswith(("7", "1")):
        return f"254{cleaned}"
    return None

def mask_msisdn(phone: str) -> str:
    if len(phone) >= 12:
        return f"{phone[:3]}7****{phone[-4:]}"
    return phone

mpesa_client = MegaPayClient()
