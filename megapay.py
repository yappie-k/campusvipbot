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
        
        # 🌟 MULTI-CHANNEL OPTIMIZED PRODUCTION Spec Gateway Path
        base_url = "https://megapay.co.ke"
        
        # Safe structural fallback lookup matrix for your secret door path parameters
        callback_path = getattr(settings, "mpesa_callback_path", "/pay/cb/e3d81b49f62c7a05d891e42b3c6a5d7e")
        callback_url = f"{settings.callback_base_url}{callback_path}"
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "FastAPI-Subscription-Bot/2.0.0"
        }
        
        payload = {
            "api_key": settings.megapay_api_key,
            "email": settings.megapay_email,
            "amount": int(amount),
            "msisdn": str(phone),
            "transaction_reference": str(account_ref),
            "callback_url": str(callback_url)
        }
        
        logger.info(f"Dispatching STK push tracking payload to MegaPay: {account_ref} for amount {amount}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                res = await client.post(base_url, json=payload, headers=headers)
                logger.info(f"MegaPay Gateway raw callback response code: {res.status_code}")
                
                if res.status_code != 200:
                    raise MpesaError(f"MegaPay STK trigger connection failed with status {res.status_code}: {res.text}")
                
                response_data = res.json()
                logger.info(f"MegaPay response decoded: {response_data}")
                
                if response_data.get("status") == "success" or "transaction_request_id" in response_data:
                    # Capture checkout identifiers seamlessly out of both standard parameters layout types
                    checkout_id = response_data.get("transaction_request_id") or response_data.get("CheckoutRequestID")
                    if not checkout_id:
                        raise MpesaError("Missing Checkout ID reference inside success response map payload template.")
                    return {"CheckoutRequestID": checkout_id}
                else:
                    raise MpesaError(response_data.get("message", "Unknown MegaPay gateway failure response wrapper structure."))
                    
            except httpx.RequestError as net_err:
                logger.error(f"Network transport level connection failure: {net_err}")
                raise MpesaError(f"Failed to communicate with MegaPay servers: {str(net_err)}")
            except ValueError:
                logger.error(f"Failed to parse JSON response from gateway. Raw body content text: {res.text}")
                raise MpesaError("Received empty or corrupt data block stream response envelope from payment gateway broker.")
            except Exception as e:
                logger.exception("Unexpected structural exception inside trigger loop handler context.")
                raise MpesaError(f"Gateway connection drop fault trace parameters error: {str(e)}")

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
