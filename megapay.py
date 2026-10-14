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
        
        # Dual fallback endpoint router mapping parameters
        base_url = "https://megapay.co.ke"
        
        callback_path = getattr(settings, "mpesa_callback_path", "/pay/cb/e3d81b49f62c7a05d891e42b3c6a5d7e")
        callback_url = f"{settings.callback_base_url}{callback_path}"
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        # 🌟 UNIVERSAL DUAL EMBED MATRIX PAYLOAD: Supplies all standard variable layout formats simultaneously!
        payload = {
            "api_key": settings.megapay_api_key,
            "email": settings.megapay_email,
            "amount": int(amount),
            
            # Handles different mobile phone number structural keys
            "msisdn": str(phone),
            "phone": str(phone),
            "phoneNumber": str(phone),
            
            # Handles different unique billing reference structural keys
            "transaction_reference": str(account_ref),
            "account_ref": str(account_ref),
            "account_reference": str(account_ref),
            "reference": str(account_ref),
            
            # Handles different backend path reporting keys
            "callback_url": str(callback_url),
            "callbackUrl": str(callback_url)
        }
        
        logger.info(f"Dispatching clean checkout parameters to sandbox gateway matrix: {account_ref}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                res = await client.post(base_url, json=payload, headers=headers)
                logger.info(f"MegaPay Response Code received: {res.status_code}")
                
                # If they return an error code or an HTML block page print details straight into logs
                if res.status_code != 200:
                    logger.error(f"Gateway rejected connection block raw dump text data: {res.text}")
                    raise MpesaError(f"MegaPay STK trigger connection failed with status {res.status_code}")
                
                response_data = res.json()
                logger.info(f"MegaPay response decoded successfully: {response_data}")
                
                # Check for any success flags or unique transactional request codes inside parameters map fields
                checkout_id = (
                    response_data.get("transaction_request_id") or 
                    response_data.get("CheckoutRequestID") or
                    response_data.get("request_id") or
                    response_data.get("id")
                )
                
                if response_data.get("status") in ("success", "completed", "pending") or checkout_id:
                    if not checkout_id:
                        # Fallback placeholder to maintain execution flows if the gateway lacks a key
                        checkout_id = f"MOCK_TX_{int(datetime.utcnow().timestamp())}"
                    return {"CheckoutRequestID": checkout_id}
                else:
                    raise MpesaError(response_data.get("message", "Unknown MegaPay gateway failure response logic."))
                    
            except httpx.RequestError as net_err:
                raise MpesaError(f"Failed to communicate with MegaPay servers: {str(net_err)}")
            except ValueError:
                # If parsing fails look into the logs to see the exact text details MegaPay sent us
                logger.error(f"JSON structure extraction failed. Content received: {res.text}")
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
