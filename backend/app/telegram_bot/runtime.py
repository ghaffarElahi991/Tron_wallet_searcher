import asyncio
import contextlib
import logging
import uuid

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from app.models import JobStatus
from app.telegram_bot.api_client import TronForgeApiClient, TronForgeApiError
from app.telegram_bot.keyboards import cancel_job_menu
from app.telegram_bot.messages import job_progress, wallet_result

logger = logging.getLogger(__name__)
TERMINAL_STATUSES = {
    JobStatus.READY,
    JobStatus.OWNERSHIP_VERIFIED,
    JobStatus.FAILED,
    JobStatus.CANCELED,
    JobStatus.TIMED_OUT,
}


class BotRuntime:
    def __init__(
        self,
        api: TronForgeApiClient,
        poll_interval: float,
        *,
        broadcast_results_to_chat: bool = False,
    ) -> None:
        self.api = api
        self.poll_interval = poll_interval
        self.broadcast_results_to_chat = broadcast_results_to_chat
        self.tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    def monitor(
        self,
        bot: Bot,
        *,
        chat_id: int,
        message_id: int,
        job_id: uuid.UUID,
        recipient_user_id: int,
    ) -> None:
        existing = self.tasks.get(job_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._monitor(
                bot,
                chat_id=chat_id,
                message_id=message_id,
                job_id=job_id,
                recipient_user_id=recipient_user_id,
            ),
            name=f"telegram-job-{job_id}",
        )
        self.tasks[job_id] = task
        task.add_done_callback(lambda _task: self.tasks.pop(job_id, None))

    async def _monitor(
        self,
        bot: Bot,
        *,
        chat_id: int,
        message_id: int,
        job_id: uuid.UUID,
        recipient_user_id: int,
    ) -> None:
        previous_text = ""
        while True:
            try:
                job = await self.api.get_job(job_id)
                text = job_progress(job)
                if text != previous_text:
                    reply_markup = (
                        None if job.status in TERMINAL_STATUSES else cancel_job_menu(job.id)
                    )
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=text,
                            reply_markup=reply_markup,
                        )
                    except TelegramBadRequest as exc:
                        if "message is not modified" not in str(exc).lower():
                            raise
                    previous_text = text
                if job.status in {JobStatus.READY, JobStatus.OWNERSHIP_VERIFIED}:
                    if self.broadcast_results_to_chat:
                        await bot.send_message(chat_id, wallet_result(job))
                        return
                    try:
                        await bot.send_message(recipient_user_id, wallet_result(job))
                    except (TelegramForbiddenError, TelegramBadRequest) as exc:
                        logger.warning(
                            "Telegram wallet %s could not be delivered privately to %s: %s",
                            job_id,
                            recipient_user_id,
                            exc,
                        )
                        if chat_id != recipient_user_id:
                            await bot.send_message(
                                chat_id,
                                "Wallet ready, but I cannot message the requester privately. "
                                "Open a private chat with this bot, send /start, then use "
                                "Recent jobs → Reveal in this group.",
                            )
                    return
                if job.status in TERMINAL_STATUSES:
                    return
            except TelegramRetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after))
                continue
            except TelegramForbiddenError:
                return
            except (TelegramBadRequest, TronForgeApiError) as exc:
                logger.warning("Telegram job monitor %s retrying: %s", job_id, exc)
            await asyncio.sleep(self.poll_interval)

    async def close(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.tasks.clear()
        await self.api.close()
