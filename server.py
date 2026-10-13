"""
server.py - FastAPI Webhook Server for MegaPay Kenya Notifications

Receives asynchronous payment callback notifications from MegaPay:
1. Flexible payload extractor: handles native MegaPay webhook schema as well as forwarded M-Pesa callbacks.
2. Updates database transaction status (SUCCESS / FAILED / CANCELLED).
3. On payment success: generates a single-use Telegram private channel invite link (member_limit=1).
4. Direct messages the customer with their private invite button.
5. Render-ready health check endpoint (GET /health).
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from telegram import Bot
from telegram.constants import ParseMode

from config import (
    TELEGRAM_CHANNEL_TITLE,
    MEGAPAY_CALLBACK_PATH,
    APP_TIMEZONE,
    get_plan_by_code,
    logger,
)
from database import (
    get_transaction_by_ref_or_id,
    mark_payment_result,
    activate_or_extend_subscription,
)
from bot import generate_single_use_invite_link, build_plans_keyboard

app = FastAPI(
    title="Campus VIP Hub MegaPay Subscription Webhook Server",
    description="Processes MegaPay M-Pesa STK Push webhook callbacks and provisions VIP channel access",
    version="3.0.0",
)

telegram_bot: Optional[Bot] = None


def set_shared_bot_instance(bot: Bot) -> None:
    global telegram_bot
    telegram_bot = bot


@app.get("/")
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "gateway": "MegaPay Kenya",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bot_initialized": telegram_bot is not None,
    }


# 🌟 THE CORRECTION: This tells your app to listen exactly on your secret link path
@app.post(MEGAPAY_CALLBACK_PATH)
async def mpesa_webhook(request: Request) -> JSONResponse:
    return await process_megapay_webhook_payload(request)


async def process_megapay_webhook_payload(request: Request) -> JSONResponse:
    try:
        raw_body_bytes = await request.body()
        raw_body_str = raw_body_bytes.decode("utf-8")
        logger.info("Received raw MegaPay Webhook payload: %s", raw_body_str)

        try:
            payload = json.loads(raw_body_str)
        except Exception:
            logger.error("Failed to parse JSON from incoming webhook payload.")
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"status": "error", "message": "Invalid JSON"},
            )

        response_code = payload.get("ResponseCode")
        response_desc = payload.get("ResponseDescription") or payload.get("message") or "Processed"
        tx_id = payload.get("TransactionID") or payload.get("transaction_id")
        receipt = payload.get("TransactionReceipt") or payload.get("mpesa_receipt") or payload.get("MpesaReceiptNumber")
        amount = payload.get("TransactionAmount") or payload.get("amount")
        reference = payload.get("Reference") or payload.get("reference")

        if response_code is None and "Body" in payload:
            stk_cb = payload.get("Body", {}).get("stkCallback", {})
            response_code = stk_cb.get("ResultCode")
            response_desc = stk_cb.get("ResultDesc", response_desc)
            checkout_id = stk_cb.get("CheckoutRequestID")
            if not tx_id:
                tx_id = checkout_id
            meta_items = stk_cb.get("CallbackMetadata", {}).get("Item", [])
            for item in meta_items:
                if item.get("Name") == "MpesaReceiptNumber":
                    receipt = item.get("Value")
                elif item.get("Name") == "Amount":
                    amount = item.get("Value")

        if response_code is None:
            st = str(payload.get("status", "")).lower()
            response_code = 0 if st in ("success", "completed", "paid") else 1

        try:
            response_code_int = int(response_code)
        except (ValueError, TypeError):
            response_code_int = 0 if str(response_code).lower() in ("true", "success") else 1

        tx = await get_transaction_by_ref_or_id(reference=reference, transaction_id=tx_id)

        if not tx:
            logger.warning(
                "No matching transaction found in database for Reference='%s' or TransactionID='%s'",
                reference,
                tx_id,
            )
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"status": "acknowledged", "message": "Transaction not found locally"},
            )

        match_ref = tx.reference

        if response_code_int == 0:
            logger.info(
                "MegaPay payment SUCCEEDED for user %d! Receipt=%s, Amount=%s",
                tx.telegram_id,
                receipt,
                amount or tx.amount,
            )

            await mark_payment_result(
                tx_id_or_ref=match_ref,
                status="SUCCESS",
                response_code=response_code_int,
                response_desc=response_desc,
                receipt_number=str(receipt) if receipt else None,
                megapay_tx_id=str(tx_id) if tx_id else None,
                raw_callback=raw_body_str,
            )

            plan = get_plan_by_code(tx.plan_code)
            duration_days = plan.get("days", 30) if plan else 30
            plan_name = plan.get("name", tx.plan_code) if plan else tx.plan_code

            invite_link: Optional[str] = None
            if telegram_bot:
                try:
                    invite_link = await generate_single_use_invite_link(
                        telegram_bot, tx.telegram_id
                    )
                except Exception as err:
                    logger.error("Could not generate invite link for user %d: %s", tx.telegram_id, err)

            sub = await activate_or_extend_subscription(
                telegram_id=tx.telegram_id,
                plan_code=tx.plan_code,
                duration_days=duration_days,
                invite_link=invite_link,
            )

            if telegram_bot:
                try:
                    local_expiry = sub.end_date.astimezone(APP_TIMEZONE).strftime("%d %b %Y, %I:%M %p EAT")
                    success_msg = (
                        "🎉 **M-PESA PAYMENT VERIFIED VIA MEGAPAY!**\n\n"
                        f"Receipt: `{receipt or tx_id or 'CONFIRMED'}`\n"
                        f"Amount: **KES {float(amount or tx.amount):,.2f}**\n"
                        f"Plan: **{plan_name}**\n"
                        f"Channel: **{TELEGRAM_CHANNEL_TITLE}**\n"
                        f"Access Until: `{local_expiry}`\n\n"
                    )

                    if invite_link:
                        success_msg += (
                            "🔑 **Your Private Single-Use Invite Link:**\n"
                            f"{invite_link}\n\n"
                            "⚠️ *Note: This link allows exactly 1 member to enter. Once clicked, it expires and cannot be shared!*"
                        )
                    else:
                        success_msg += (
                            "✅ Your membership has been activated! Contact admin if you need a link."
                        )

                    await telegram_bot.send_message(
                        chat_id=tx.telegram_id,
                        text=success_msg,
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception as send_err:
                    logger.error("Failed to send success DM to user %d: %s", tx.telegram_id, send_err)

        else:
            status_label = "CANCELLED" if response_code_int == 1032 else "FAILED"
            logger.warning(
                "MegaPay payment %s for user %d (Code=%d, Desc=%s)",
                status_label,
                tx.telegram_id,
                response_code_int,
                response_desc,
            )

            await mark_payment_result(
                tx_id_or_ref=match_ref,
                status=status_label,
                response_code=response_code_int,
                response_desc=response_desc,
                megapay_tx_id=str(tx_id) if tx_id else None,
                raw_callback=raw_body_str,
            )

            if telegram_bot:
                # 🌟 THE CORRECTION: Safely handles payment cancellations/failures with clean text
                try:
                    failure_msg = (
                        "❌ **M-PESA TRANSACTION NOT COMPLETED**\n\n"
                        f"Your checkout request for plan **{tx.plan_code}** did not go through.\n"
                        f"⚠️ **Reason:** `{response_desc}`\n\n"
                        "Please verify your wallet balance and run /start to try again."
                    )
                    await telegram_bot.send_message(
                        chat_id=tx.telegram_id,
                        text=failure_msg,
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception as send_err:
                    logger.error("Failed to send failure alert DM to user %d: %s", tx.telegram_id, send_err)

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "success", "message": "Callback processed completely"}
        )

    except Exception as global_err:
        logger.error("Unhandled critical error inside webhook loop: %s", global_err)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "error", "message": "Internal gateway error"}
        )
