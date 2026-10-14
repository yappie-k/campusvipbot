"""Application Entrance Coordinator Bootstrapper Engine."""
from __future__ import annotations

import asyncio
import logging
import sys
import uvicorn

from config import get_settings
from database import init_db
from bot import build_application
from server import app as fastapi_app
from scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


async def run() -> None:
    settings = get_settings()
    logger.info(
        f"Initializing multi-threaded architectural bot loops running on "
        f"MegaPay engine framework integration layer... "
        f"Target token: {settings.telegram_bot_token[:10]}..."
    )

    # 1. Initialize SQLite Database storage schemas tables
    await init_db()
    logger.info("Database schemas fully synchronized.")

    # 2. Extract and mount the long polling bot instance engine
    tg_application = build_application()

    # 3. Start background scheduler clocks loops
    start_scheduler()

    # 4. Bind bot handlers pipelines
    await tg_application.initialize()
    await tg_application.start()
    await tg_application.updater.start_polling()
    logger.info("Telegram polling loop listeners active.")

    # 5. Bring up ASGI webserver endpoint parameters listener gateway
    uvicorn_config = uvicorn.Config(
        app=fastapi_app,
        host=settings.http_host,
        port=settings.http_port,
        log_level="info",
    )
    uvicorn_server = uvicorn.Server(uvicorn_config)

    try:
        await uvicorn_server.serve()
    finally:
        logger.info("De-initializing background operational threads safely...")
        await tg_application.updater.stop()
        await tg_application.stop()
        await tg_application.shutdown()
        stop_scheduler()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Asynchronous main pipeline loops safely shut down.")


if __name__ == "__main__":
    main()
