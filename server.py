"""FastAPI Webhook Server Engine: Intercepts MegaPay multi-channel callbacks."""
from __future__ import annotations

import logging
from fastapi import FastAPI, Request

from config import get_settings
from database import activate_subscription_from_payment, get_payment_by_checkout, session_scope

logger = logging.getLogger(__name__)
app = FastAPI(title="MegaPay Automated Webhook Endpoint")

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok"}

@app.post("/pay/cb/e3d81b49f62c7a05d891e42b3c6a5d7e")
async def mpesa_webhook(request: Request):
    """Processes instant payment notification records dispatched via MegaPay."""
    payload = await request.json()
    result_code = int(payload.get("ResponseCode", -1))
    result_desc = payload.get("ResponseDescription", "Transaction abandoned.")
    checkout_id = payload.get("CheckoutRequestID")

    logger.info(f"MegaPay callback hit for CheckoutID: {checkout_id}, ResultCode: {result_code}")

    # 🌟 RUNTIME INLINE IMPORT DECOUPLES CIRCULAR LOCK ENGINES
    from bot import fulfill_successful_payment, notify_failed_payment

    async with session_scope() as session:
        payment = await get_payment_by_checkout(session, checkout_id)
        if not payment:
            return {"status": "ignored"}

        if result_code == 0:
            # Payment Succeeded
            subscriptions = await activate_subscription_from_payment(session, payment)
            await session.commit()
            
            # Grabs expiration time parameters out of the first created subscription row entry safely
            primary_sub = subscriptions[0] if isinstance(subscriptions, list) else subscriptions
            await fulfill_successful_payment(
                telegram_id=payment.telegram_id,
                channel_id=payment.channel_id,
                plan_code=payment.plan_code,
                expires_at=primary_sub.expires_at
            )
        else:
            # Payment Failed
            payment.status = "failed"
            await session.commit()
            await notify_failed_payment(
                telegram_id=payment.telegram_id, 
                plan_code=payment.plan_code, 
                reason=result_desc
            )

    return {"status": "success"}
