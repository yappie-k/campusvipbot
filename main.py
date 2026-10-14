"""
main.py - Master Application Entry Point

Coordinates the entire lifecycle inside a single unified asyncio event loop:
1. Initializes Database schema (PostgreSQL for Render / SQLite fallback).
2. Initializes python-telegram-bot v20+ polling engine.
3. Injects shared Telegram Bot instance into FastAPI webhook server.
4. Starts APScheduler background expulsion worker.
5. Runs Uvicorn ASGI HTTP Server binding to Render's $PORT or 8000.
6. Handles graceful teardown on SIGINT/SIGTERM.
"""

import asyncio
import signal
import sys
import uvicorn

from config import (
    HTTP_HOST,
    SERVER_PORT,
    MEGAPAY_CALLBACK_PATH,
    WEBHOOK_PUBLIC_URL,
    logger,
)
from database import init_db, engine
from bot import create_telegram_bot_app
from server import app as fastapi_app, set_shared_bot_instance
from scheduler import start_expulsion_scheduler


async def run_application():
    logger.info("=====================================================")
    logger.info("Starting Campus VIP MegaPay Subscription Service")
    logger.info("=====================================================")

    try:
        await init_db()
    except Exception as e:
        logger.critical("Database initialization failed: %s", e)
        sys.exit(1)

    try:
        ptb_app = create_telegram_bot_app()
        await ptb_app.initialize()
        await ptb_app.start()
        await ptb_app.updater.start_polling(drop_pending_updates=True)
        bot_info = await ptb_app.bot.get_me()
        logger.info("Telegram Bot connected successfully as @%s (ID: %d)", bot_info.username, bot_info.id)
    except Exception as e:
        logger.critical("Failed to start Telegram Bot: %s", e)
        sys.exit(1)

    set_shared_bot_instance(ptb_app.bot)

    scheduler = start_expulsion_scheduler(ptb_app.bot)

    uvicorn_config = uvicorn.Config(
        app=fastapi_app,
        host=HTTP_HOST,
        port=SERVER_PORT,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(uvicorn_config)

    stop_event = asyncio.Event()

    def signal_handler():
        logger.info("Shutdown signal received. Initiating graceful teardown...")
        stop_event.set()
        server.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass

    logger.info("FastAPI Webhook Server listening on http://%s:%d", HTTP_HOST, SERVER_PORT)
    logger.info("MegaPay Webhook route mounted at POST %s", MEGAPAY_CALLBACK_PATH)
    logger.info("Target Webhook URL for MegaPay Dashboard: %s", WEBHOOK_PUBLIC_URL)
    logger.info("Application is fully initialized and operational.")

    server_task = asyncio.create_task(server.serve())

    try:
        await server_task
    except asyncio.CancelledError:
        logger.info("Server task cancelled.")
    finally:
        logger.info("Cleaning up application components...")

        try:
            scheduler.shutdown(wait=False)
            logger.info("APScheduler stopped.")
        except Exception as e:
            logger.error("Error shutting down scheduler: %s", e)

        try:
            if ptb_app.updater and ptb_app.updater.running:
                await ptb_app.updater.stop()
            if ptb_app.running:
                await ptb_app.stop()
            await ptb_app.shutdown()
            logger.info("Telegram Bot polling stopped and engine shut down.")
        except Exception as e:
            logger.error("Error stopping Telegram Bot: %s", e)

        try:
            await engine.dispose()
            logger.info("Database engine connections closed.")
        except Exception as e:
            logger.error("Error disposing database engine: %s", e)

        logger.info("All services terminated cleanly. Goodbye!")


def main():
    try:
        asyncio.run(run_application())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Process interrupted. Exiting.")


if __name__ == "__main__":
    main()
