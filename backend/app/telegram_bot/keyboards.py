import uuid

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import PatternType


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Generate wallet", callback_data="menu:new")],
            [
                InlineKeyboardButton(text="Recent jobs", callback_data="menu:jobs"),
                InlineKeyboardButton(text="GPU status", callback_data="menu:gpus"),
            ],
        ]
    )


def pattern_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="2 × 2", callback_data="pattern:2x2"),
                InlineKeyboardButton(text="3 × 4", callback_data="pattern:3x4"),
            ],
            [
                InlineKeyboardButton(text="2 × 5", callback_data="pattern:2x5"),
                InlineKeyboardButton(text="4 × 3", callback_data="pattern:4x3"),
            ],
            [InlineKeyboardButton(text="Back", callback_data="menu:home")],
        ]
    )


def confirmation_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Start generation", callback_data="form:create")],
            [InlineKeyboardButton(text="Cancel", callback_data="form:cancel")],
        ]
    )


def cancel_job_menu(job_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Cancel generation", callback_data=f"job:cancel:{job_id}")]
        ]
    )


def recent_jobs_menu(
    jobs: list[tuple[uuid.UUID, str, str]],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    verbs = {"watch": "Watch", "cancel": "Cancel", "reveal": "Show"}
    for job_id, label, action in jobs:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{verbs[action]} {label}", callback_data=f"job:{action}:{job_id}"
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Back", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pattern_label(pattern: PatternType) -> str:
    return pattern.value.replace("x", " × ")
