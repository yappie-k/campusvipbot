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
        
        # 🌟 CORRECT OFFICIAL DIRECT API ROUTE ENDPOINT
        base_url = "https://megapay.co.ke/backend/v1/initiatestk"
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        payload = {
            "api_key": str(settings.megapay_api_key),
            "email": str(settings.megapay_email),
            "amount": int(amount),
            "msisdn": str(phone),
            "reference": str(account_ref)
        }
        
        logger.info(f"Dispatching parameter token to gateway: {account_ref} for KES {int(amount)}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                res = await client.post(base_url, json=payload, headers=headers)
                logger.info(f"MegaPay Response Code received: {res.status_code}")
                
                if res.status_code != 200:
                    logger.error(f"Gateway rejected data payload raw dump: {res.text}")
                    raise MpesaError(f"MegaPay STK trigger connection failed with status {res.status_code}")
                
                response_data = res.json()
                logger.info(f"MegaPay response decoded successfully: {response_data}")
                
                if response_data.get("success") == "200" or "transaction_request_id" in response_data:
                    checkout_id = response_data.get("transaction_request_id")
                    if not checkout_id:
                        raise MpesaError("Missing transaction_request_id reference inside success payload.")
                    return {"CheckoutRequestID": checkout_id}
                else:
                    raise MpesaError(response_data.get("massage") or response_data.get("message", "Unknown MegaPay failure response."))
                    
            except httpx.RequestError as net_err:
                raise MpesaError(f"Failed to communicate with MegaPay servers: {str(net_err)}")
            except ValueError:
                logger.error(f"JSON extraction failed. Content received: {res.text}")
                raise MpesaError("Received empty or corrupt data block stream response envelope from payment gateway broker.")
            except Exception as e:
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
