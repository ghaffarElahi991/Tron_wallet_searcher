import re
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any

from aiogram import BaseMiddleware, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ForceReply, Message, TelegramObject

from app.models import JobStatus, PatternType
from app.telegram_bot.api_client import TronForgeApiError
from app.telegram_bot.keyboards import (
    confirmation_menu,
    funding_confirmation_menu,
    main_menu,
    pattern_label,
    pattern_menu,
    recent_jobs_menu,
    wallet_result_menu,
)
from app.telegram_bot.messages import gpu_fleet_message, job_progress, wallet_result
from app.telegram_bot.runtime import BotRuntime
from app.telegram_bot.validation import (
    PatternInputError,
    validate_custom_prefix,
    validate_suffix,
)


class WalletForm(StatesGroup):
    prefix = State()
    suffix = State()
    confirmation = State()


class FundingForm(StatesGroup):
    amount = State()


FUNDING_AMOUNT = re.compile(r"^\d+(?:\.\d{1,6})?$")


def wallet_commands_allowed(
    *,
    user_id: int | None,
    is_bot: bool,
    chat_id: int | None,
    chat_type: str | None,
    allowed_user_id: int,
    restrict_user_id: bool,
    allowed_group_id: int,
    public_access: bool = False,
) -> bool:
    if user_id is None or is_bot:
        return False
    if public_access:
        return chat_type in {"private", "group", "supergroup"}
    user_allowed = not restrict_user_id or (allowed_user_id > 0 and user_id == allowed_user_id)
    if not user_allowed:
        return False
    if chat_type == "private":
        return restrict_user_id
    return (
        chat_type in {"group", "supergroup"}
        and allowed_group_id != 0
        and chat_id == allowed_group_id
    )


def _is_start_message(event: TelegramObject) -> bool:
    if not isinstance(event, Message) or not event.text:
        return False
    return event.text.split(maxsplit=1)[0].split("@", 1)[0] == "/start"


class OperatorOnlyMiddleware(BaseMiddleware):
    def __init__(
        self,
        allowed_user_id: int,
        *,
        restrict_user_id: bool,
        allowed_group_id: int,
        public_access: bool,
    ) -> None:
        self.allowed_user_id = allowed_user_id
        self.restrict_user_id = restrict_user_id
        self.allowed_group_id = allowed_group_id
        self.public_access = public_access

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        chat = data.get("event_chat")
        authorized = wallet_commands_allowed(
            user_id=user.id if user is not None else None,
            is_bot=user.is_bot if user is not None else False,
            chat_id=chat.id if chat is not None else None,
            chat_type=chat.type if chat is not None else None,
            allowed_user_id=self.allowed_user_id,
            restrict_user_id=self.restrict_user_id,
            allowed_group_id=self.allowed_group_id,
            public_access=self.public_access,
        )
        if authorized:
            return await handler(event, data)
        if (
            not self.restrict_user_id
            and not self.public_access
            and chat is not None
            and chat.type == "private"
            and _is_start_message(event)
        ):
            # Telegram requires a private conversation before the bot can deliver a key by DM.
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer(
                "Wallet controls are available only in the configured group.", show_alert=True
            )
        elif isinstance(event, Message) and event.from_user is not None:
            if (
                event.chat.type in {"group", "supergroup"}
                and _is_start_message(event)
                and (
                    not self.restrict_user_id
                    or self.allowed_user_id == 0
                    or event.from_user.id == self.allowed_user_id
                )
            ):
                await event.answer(
                    "To authorize this group, set this value in backend/.env "
                    "and restart the bot:\n\n"
                    f"<code>TRONFORGE_TELEGRAM_ALLOWED_GROUP_ID={event.chat.id}</code>"
                )
            elif (
                self.restrict_user_id
                and self.allowed_user_id == 0
                and event.chat.type == "private"
            ):
                await event.answer(
                    "Bot setup is incomplete. Add this value to "
                    "backend/app/local_constants.py and restart:\n\n"
                    f"<code>TELEGRAM_ALLOWED_USER_ID = {event.from_user.id}</code>"
                )
            else:
                await event.answer("Access denied.")
        return None


def create_router(
    allowed_user_id: int,
    *,
    restrict_user_id: bool = True,
    allowed_group_id: int = 0,
    public_access: bool = False,
    funding_enabled: bool = False,
) -> Router:
    router = Router(name="tronforge-operator")
    middleware = OperatorOnlyMiddleware(
        allowed_user_id,
        restrict_user_id=restrict_user_id,
        allowed_group_id=allowed_group_id,
        public_access=public_access,
    )
    router.message.middleware(middleware)
    router.callback_query.middleware(middleware)

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        if not public_access and not restrict_user_id and message.chat.type == "private":
            await message.answer(
                "Private delivery is ready. Return to the configured group to request "
                "or reveal wallets."
            )
            return
        await message.answer(
            "<b>TronForge Wallet Generator</b>\n\n"
            "Create a TRON vanity wallet using every available GPU on this server.",
            reply_markup=main_menu(),
        )

    @router.message(Command("cancel"))
    async def cancel_form(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Wallet form canceled.", reply_markup=main_menu())

    @router.callback_query(F.data == "menu:home")
    async def home(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        if callback.message is not None:
            await callback.message.edit_text(
                "<b>TronForge Wallet Generator</b>\n\nChoose an action.",
                reply_markup=main_menu(),
            )
        await callback.answer()

    @router.callback_query(F.data == "menu:new")
    async def choose_pattern(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        if callback.message is not None:
            await callback.message.edit_text(
                "<b>Choose a wallet pattern</b>\n\n"
                "The first number is the prefix length after T. The second is the suffix length.",
                reply_markup=pattern_menu(),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("pattern:"))
    async def receive_pattern(callback: CallbackQuery, state: FSMContext) -> None:
        raw_pattern = (callback.data or "").split(":", 1)[1]
        try:
            pattern = PatternType(raw_pattern)
        except ValueError:
            await callback.answer("Unsupported pattern.", show_alert=True)
            return
        await state.set_state(WalletForm.prefix)
        await state.update_data(pattern=pattern.value)
        custom_length = int(pattern.value.split("x", 1)[0])
        if callback.message is not None:
            prompt = (
                f"<b>{pattern_label(pattern)} prefix</b>\n\n"
                f"Send exactly <b>{custom_length}</b> characters after the fixed <b>T</b>.\n"
                "The first character must be uppercase or 9. Do not type the T."
            )
            if callback.message.chat.type in {"group", "supergroup"}:
                await callback.message.answer(
                    f"{prompt}\n\nReply to this message with your prefix.",
                    reply_markup=ForceReply(input_field_placeholder="Prefix after T"),
                )
            else:
                await callback.message.edit_text(prompt)
        await callback.answer()

    @router.message(WalletForm.prefix, F.text)
    async def receive_prefix(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        pattern = PatternType(data["pattern"])
        try:
            prefix = validate_custom_prefix(pattern, message.text or "")
        except PatternInputError as exc:
            await message.answer(
                f"Invalid prefix: {escape(str(exc))}",
                reply_markup=(
                    ForceReply(input_field_placeholder="Try prefix again")
                    if message.chat.type in {"group", "supergroup"}
                    else None
                ),
            )
            return
        await state.update_data(prefix=prefix)
        await state.set_state(WalletForm.suffix)
        suffix_length = int(pattern.value.split("x", 1)[1])
        group_chat = message.chat.type in {"group", "supergroup"}
        suffix_prompt = (
            f"Prefix accepted: <code>{escape(prefix)}</code>\n\n"
            f"Now send exactly <b>{suffix_length}</b> suffix characters."
        )
        if group_chat:
            suffix_prompt += " Reply to this message."
        await message.answer(
            suffix_prompt,
            reply_markup=ForceReply(input_field_placeholder="Suffix") if group_chat else None,
        )

    @router.message(WalletForm.suffix, F.text)
    async def receive_suffix(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        pattern = PatternType(data["pattern"])
        try:
            suffix = validate_suffix(pattern, message.text or "")
        except PatternInputError as exc:
            await message.answer(
                f"Invalid suffix: {escape(str(exc))}",
                reply_markup=(
                    ForceReply(input_field_placeholder="Try suffix again")
                    if message.chat.type in {"group", "supergroup"}
                    else None
                ),
            )
            return
        await state.update_data(suffix=suffix)
        await state.set_state(WalletForm.confirmation)
        prefix = str(data["prefix"])
        middle = "•" * max(5, 34 - len(prefix) - len(suffix))
        await message.answer(
            "<b>Confirm generation</b>\n\n"
            f"Pattern: <b>{pattern_label(pattern)}</b>\n"
            f"Preview: <code>{escape(prefix)}{middle}{escape(suffix)}</code>\n\n"
            "All available GPUs will work on this job.",
            reply_markup=confirmation_menu(),
        )

    @router.callback_query(F.data == "form:cancel")
    async def cancel_confirmation(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        if callback.message is not None:
            await callback.message.edit_text("Wallet form canceled.", reply_markup=main_menu())
        await callback.answer()

    @router.callback_query(F.data == "form:create")
    async def create_job(
        callback: CallbackQuery,
        state: FSMContext,
        runtime: BotRuntime,
    ) -> None:
        if callback.message is None:
            await callback.answer("Message is unavailable.", show_alert=True)
            return
        data = await state.get_data()
        try:
            pattern = PatternType(data["pattern"])
            prefix = str(data["prefix"])
            suffix = str(data["suffix"])
        except (KeyError, ValueError):
            await state.clear()
            await callback.answer("The form expired. Start again.", show_alert=True)
            return
        await state.clear()
        await callback.answer("Creating job…")
        await callback.message.edit_text("Creating the generation job…")
        try:
            job = await runtime.api.create_job(pattern, prefix, suffix)
        except TronForgeApiError as exc:
            await callback.message.edit_text(
                f"Could not create the job: {escape(str(exc))}", reply_markup=main_menu()
            )
            return
        progress = await callback.message.edit_text(
            "Generation job created. Waiting for scheduler…"
        )
        runtime.monitor(
            callback.bot,
            chat_id=progress.chat.id,
            message_id=progress.message_id,
            job_id=job.id,
            recipient_user_id=callback.from_user.id,
        )

    @router.callback_query(F.data == "menu:gpus")
    async def gpu_status(callback: CallbackQuery, runtime: BotRuntime) -> None:
        if callback.message is None:
            await callback.answer()
            return
        try:
            fleet = await runtime.api.get_gpu_fleet()
            await callback.message.edit_text(gpu_fleet_message(fleet), reply_markup=main_menu())
        except TronForgeApiError as exc:
            await callback.message.edit_text(
                f"Could not load GPU status: {escape(str(exc))}", reply_markup=main_menu()
            )
        await callback.answer()

    @router.callback_query(F.data == "menu:jobs")
    async def recent_jobs(callback: CallbackQuery, runtime: BotRuntime) -> None:
        if callback.message is None:
            await callback.answer()
            return
        try:
            response = await runtime.api.list_jobs(limit=8)
        except TronForgeApiError as exc:
            await callback.message.edit_text(
                f"Could not load jobs: {escape(str(exc))}", reply_markup=main_menu()
            )
            await callback.answer()
            return
        if not response.items:
            await callback.message.edit_text("No generation jobs yet.", reply_markup=main_menu())
            await callback.answer()
            return
        lines = ["<b>Recent generation jobs</b>", ""]
        buttons: list[tuple[uuid.UUID, str, str]] = []
        for job in response.items:
            lines.append(
                f"<code>{str(job.id)[:8]}</code> · {job.pattern.value} · <b>{job.status.value}</b>"
            )
            if job.status in {JobStatus.QUEUED, JobStatus.SEARCHING, JobStatus.VERIFYING}:
                buttons.append((job.id, str(job.id)[:8], "watch"))
                buttons.append((job.id, str(job.id)[:8], "cancel"))
            elif job.status in {JobStatus.READY, JobStatus.OWNERSHIP_VERIFIED} and job.result:
                buttons.append((job.id, str(job.id)[:8], "reveal"))
                if funding_enabled:
                    buttons.append((job.id, str(job.id)[:8], "fund"))
        await callback.message.edit_text(
            "\n".join(lines), reply_markup=recent_jobs_menu(buttons)
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("job:cancel:"))
    async def cancel_job(callback: CallbackQuery, runtime: BotRuntime) -> None:
        if callback.message is None:
            await callback.answer()
            return
        try:
            job_id = uuid.UUID((callback.data or "").rsplit(":", 1)[1])
            await runtime.api.cancel_job(job_id)
        except (ValueError, TronForgeApiError) as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.message.edit_text("Generation canceled.", reply_markup=main_menu())
        await callback.answer()

    @router.callback_query(F.data.startswith("job:watch:"))
    async def watch_job(callback: CallbackQuery, runtime: BotRuntime) -> None:
        if callback.message is None:
            await callback.answer()
            return
        try:
            job_id = uuid.UUID((callback.data or "").rsplit(":", 1)[1])
            job = await runtime.api.get_job(job_id)
        except (ValueError, TronForgeApiError) as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        progress = await callback.message.edit_text(job_progress(job))
        runtime.monitor(
            callback.bot,
            chat_id=progress.chat.id,
            message_id=progress.message_id,
            job_id=job.id,
            recipient_user_id=callback.from_user.id,
        )
        await callback.answer("Monitoring resumed.")

    @router.callback_query(F.data.startswith("job:reveal:"))
    async def reveal_job(callback: CallbackQuery, runtime: BotRuntime) -> None:
        try:
            job_id = uuid.UUID((callback.data or "").rsplit(":", 1)[1])
            job = await runtime.api.get_job(job_id)
            if job.result is None:
                raise TronForgeApiError("This wallet is not ready.")
            shared_group = (
                not restrict_user_id
                and callback.message is not None
                and callback.message.chat.type in {"group", "supergroup"}
                and allowed_group_id != 0
                and callback.message.chat.id == allowed_group_id
            )
            result_chat_id = (
                callback.message.chat.id
                if (public_access or shared_group) and callback.message is not None
                else callback.from_user.id
            )
            result_menu = wallet_result_menu(
                job.id,
                funding_enabled=funding_enabled,
            )
            if result_menu is None:
                await callback.bot.send_message(result_chat_id, wallet_result(job))
            else:
                await callback.bot.send_message(
                    result_chat_id, wallet_result(job), reply_markup=result_menu
                )
        except (TelegramForbiddenError, TelegramBadRequest):
            await callback.answer(
                "Cannot send the wallet to this chat."
                if public_access or not restrict_user_id
                else "Open this bot in private, send /start, then press Reveal again.",
                show_alert=True,
            )
            return
        except (ValueError, TronForgeApiError) as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.answer("Wallet data sent.")

    @router.callback_query(F.data.startswith("job:fund:"))
    async def start_funding(callback: CallbackQuery, state: FSMContext) -> None:
        if not funding_enabled:
            await callback.answer("Telegram funding is disabled.", show_alert=True)
            return
        if callback.message is None:
            await callback.answer("Message is unavailable.", show_alert=True)
            return
        try:
            job_id = uuid.UUID((callback.data or "").rsplit(":", 1)[1])
        except ValueError:
            await callback.answer("Invalid wallet job.", show_alert=True)
            return
        await state.clear()
        await state.set_state(FundingForm.amount)
        await state.update_data(funding_job_id=str(job_id))
        await callback.message.answer(
            "<b>Fund wallet</b>\n\nSend an amount from <b>1 to 1,500 USDT</b> "
            "with no more than six decimal places.",
            reply_markup=ForceReply(input_field_placeholder="USDT amount"),
        )
        await callback.answer()

    @router.message(FundingForm.amount, F.text)
    async def receive_funding_amount(message: Message, state: FSMContext) -> None:
        raw = (message.text or "").strip()
        try:
            amount = Decimal(raw)
        except InvalidOperation:
            amount = Decimal(0)
        if not FUNDING_AMOUNT.fullmatch(raw) or not Decimal("1") <= amount <= Decimal("1500"):
            await message.answer(
                "Invalid amount. Send a value between 1 and 1,500 with up to six decimals.",
                reply_markup=ForceReply(input_field_placeholder="USDT amount"),
            )
            return
        data = await state.get_data()
        try:
            job_id = uuid.UUID(str(data["funding_job_id"]))
        except (KeyError, ValueError):
            await state.clear()
            await message.answer("The funding form expired. Open the wallet again.")
            return
        await state.update_data(funding_amount=raw)
        await message.answer(
            "<b>Confirm USDT funding</b>\n\n"
            f"Amount: <b>{escape(raw)} USDT</b>\n"
            f"Wallet job: <code>{str(job_id)[:8]}</code>\n\n"
            "The request becomes irreversible after its signed transaction is broadcast.",
            reply_markup=funding_confirmation_menu(job_id),
        )

    @router.callback_query(F.data == "funding:cancel")
    async def cancel_funding(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        if callback.message is not None:
            await callback.message.edit_text("Funding request canceled.", reply_markup=main_menu())
        await callback.answer()

    @router.callback_query(F.data.startswith("funding:confirm:"))
    async def confirm_funding(
        callback: CallbackQuery,
        state: FSMContext,
        runtime: BotRuntime,
    ) -> None:
        if (
            not funding_enabled
            or callback.message is None
        ):
            await callback.answer("Telegram funding is disabled.", show_alert=True)
            return
        data = await state.get_data()
        try:
            job_id = uuid.UUID((callback.data or "").rsplit(":", 1)[1])
            if str(data["funding_job_id"]) != str(job_id):
                raise ValueError
            amount = str(data["funding_amount"])
        except (KeyError, ValueError):
            await state.clear()
            await callback.answer("The funding form expired. Start again.", show_alert=True)
            return
        await callback.answer("Submitting funding request…")
        try:
            funding = await runtime.api.create_funding(job_id, amount)
        except TronForgeApiError as exc:
            await callback.message.edit_text(
                f"Could not create funding request: {escape(str(exc))}",
                reply_markup=main_menu(),
            )
            return
        await state.clear()
        progress = await callback.message.edit_text("Funding request created. Waiting for signer…")
        runtime.monitor_funding(
            callback.bot,
            chat_id=progress.chat.id,
            message_id=progress.message_id,
            job_id=funding.job_id,
        )

    return router
