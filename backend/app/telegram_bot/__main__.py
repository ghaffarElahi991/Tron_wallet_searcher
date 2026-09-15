import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.config import get_settings
from app.telegram_bot.api_client import TronForgeApiClient
from app.telegram_bot.handlers import create_router
from app.telegram_bot.runtime import BotRuntime


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
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(
        create_router(
            settings.telegram_allowed_user_id,
            restrict_user_id=settings.telegram_restrict_user_id,
            allowed_group_id=settings.telegram_allowed_group_id,
            public_access=settings.telegram_public_access,
        )
    )
    try:
        await api.authenticate()
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Open TronForge"),
                BotCommand(command="cancel", description="Cancel the current form"),
            ]
        )
        await dispatcher.start_polling(
            bot,
            runtime=runtime,
            allowed_updates=dispatcher.resolve_used_update_types(),
            tasks_concurrency_limit=20,
        )
    finally:
        await runtime.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
