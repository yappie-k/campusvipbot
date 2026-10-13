"""
megapay.py - MegaPay Kenya M-Pesa STK Push Integration Client

Handles:
1. Strict Kenyan phone normalization (2547XXXXXXXX / 2541XXXXXXXX).
2. Direct dispatch to MegaPay STK Push Gateway (https://megapay.co.ke/backend/v1/initiatestk).
3. No complex OAuth / passkey computation required: uses API Key + Account Email.
4. Transaction reference tracking for webhook reconciliation.
"""

import re
import time
from typing import Dict, Any, Optional
import httpx

from config import (
    MEGAPAY_API_KEY,
    MEGAPAY_EMAIL,
    MEGAPAY_INITIATE_URL,
    logger,
)


def sanitize_phone_number(raw_phone: str) -> str:
    if not raw_phone:
        raise ValueError("Phone number cannot be empty.")

    cleaned = re.sub(r"[\s\-\(\)\+]", "", str(raw_phone).strip())

    if cleaned.startswith("0") and len(cleaned) == 10:
        cleaned = "254" + cleaned[1:]
    elif cleaned.startswith("7") and len(cleaned) == 9:
        cleaned = "254" + cleaned
    elif cleaned.startswith("1") and len(cleaned) == 9:
        cleaned = "254" + cleaned
    elif cleaned.startswith("254") and len(cleaned) == 12:
        pass
    else:
        raise ValueError(
            f"Invalid phone number format: '{raw_phone}'. Must be a valid Kenyan mobile number (e.g., 0712345678 or +254712345678)."
        )

    if not re.match(r"^254(7|1)\d{8}$", cleaned):
        raise ValueError(
            f"Invalid mobile operator format: '{cleaned}'. Number must start with 2547 or 2541 followed by 8 digits."
        )

    return cleaned


async def trigger_megapay_stk_push(
    phone_number: str,
    amount: int,
    reference: str,
) -> Dict[str, Any]:
    sanitized_phone = sanitize_phone_number(phone_number)

    if not MEGAPAY_API_KEY:
        raise ValueError("MEGAPAY_API_KEY is not configured in .env file.")

    if not MEGAPAY_EMAIL:
        raise ValueError("MEGAPAY_EMAIL is not configured in .env file.")

    int_amount = int(round(amount))
    if int_amount <= 0:
        raise ValueError("Transaction amount must be greater than zero.")

    payload = {
        "api_key": MEGAPAY_API_KEY,
        "email": MEGAPAY_EMAIL,
        "amount": int_amount,
        "msisdn": sanitized_phone,
        "reference": reference,
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    logger.info(
        "Initiating MegaPay STK Push: Phone=%s, Amount=KES %d, Reference=%s",
        sanitized_phone,
        int_amount,
        reference,
    )

    async with httpx.AsyncClient(timeout=25.0) as client:
        try:
            response = await client.post(
                MEGAPAY_INITIATE_URL,
                json=payload,
                headers=headers,
            )
            response_data = response.json()

            logger.info("MegaPay API HTTP %d response: %s", response.status_code, response_data)

            if response.status_code not in (200, 201):
                err_msg = response_data.get("ResponseDescription") or response_data.get("message") or response.text
                raise RuntimeError(f"MegaPay STK Push rejected: {err_msg}")

            res_code = response_data.get("ResponseCode")
            if res_code is not None and str(res_code) not in ("0", "200"):
                err_desc = response_data.get("ResponseDescription", "Transaction failed to initialize.")
                raise RuntimeError(f"MegaPay Error ({res_code}): {err_desc}")

            return response_data

        except httpx.HTTPStatusError as e:
            logger.error("MegaPay HTTP Status Error: %s | Body: %s", e, e.response.text)
            raise RuntimeError(f"MegaPay HTTP Error: {e.response.text}") from e
        except Exception as e:
            logger.error("Failed to connect to MegaPay STK Push API: %s", e)
            raise
