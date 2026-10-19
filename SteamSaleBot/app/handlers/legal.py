from html import escape

from aiogram import Router, types
from aiogram.filters import Command

from app.config import settings


router = Router(name="legal")


def _link(label: str, url: str | None) -> str:
    return f'<a href="{escape(url, quote=True)}">{label}</a>' if url else f"{label}: ссылка ещё не настроена"


@router.message(Command("privacy"))
async def cmd_privacy(message: types.Message) -> None:
    await message.answer(_link("Политика конфиденциальности", settings.privacy_url))


@router.message(Command("terms"))
async def cmd_terms(message: types.Message) -> None:
    await message.answer(_link("Условия использования и оплаты", settings.terms_url))


@router.message(Command("support"))
async def cmd_support(message: types.Message) -> None:
    await message.answer(_link("Поддержка", settings.support_url))
