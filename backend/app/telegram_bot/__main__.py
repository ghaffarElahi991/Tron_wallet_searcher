import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.config import get_settings
from app.telegram_bot.api_client import TronForgeApiClient
from app.telegram_bot.handlers import create_router
from app.telegram_bot.runtime import BotRuntime

logger = logging.getLogger(__name__)
STARTUP_RETRY_INITIAL_SECONDS = 2.0
STARTUP_RETRY_MAX_SECONDS = 60.0


async def initialize_telegram(bot: Bot) -> None:
    commands = [
        BotCommand(command="start", description="Open TronForge"),
        BotCommand(command="cancel", description="Cancel the current form"),
    ]
    retry_delay = STARTUP_RETRY_INITIAL_SECONDS
    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            await bot.set_my_commands(commands)
            return
        except TelegramRetryAfter as exc:
            retry_after = max(float(exc.retry_after), retry_delay)
            logger.warning("Telegram startup rate-limited; retrying in %.1f seconds.", retry_after)
            await asyncio.sleep(retry_after)
        except (TelegramNetworkError, TelegramServerError) as exc:
            logger.warning(
                "Telegram startup unavailable (%s); retrying in %.1f seconds.",
                exc,
                retry_delay,
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, STARTUP_RETRY_MAX_SECONDS)


async def run() -> None:
    settings = get_settings()
    token = settings.telegram_bot_token.get_secret_value().strip()
    if not token:
        raise RuntimeError(
            "TRONFORGE_TELEGRAM_BOT_TOKEN is empty. Create a bot with BotFather and configure it."
        )

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    api = TronForgeApiClient(settings)
    runtime = BotRuntime(
        api,
        settings.telegram_poll_interval_seconds,
        broadcast_results_to_chat=settings.telegram_public_access,
        funding_enabled=settings.telegram_funding_enabled,
        funding_operator_user_id=settings.telegram_allowed_user_id,
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(
        create_router(
            settings.telegram_allowed_user_id,
            restrict_user_id=settings.telegram_restrict_user_id,
            allowed_group_id=settings.telegram_allowed_group_id,
            public_access=settings.telegram_public_access,
            funding_enabled=settings.telegram_funding_enabled,
        )
    )
    try:
        await api.authenticate()
        await initialize_telegram(bot)
        await dispatcher.start_polling(
            bot,
            runtime=runtime,
            allowed_updates=dispatcher.resolve_used_update_types(),
            tasks_concurrency_limit=20,
        )
    finally:
        await runtime.close()
        await bot.session.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
