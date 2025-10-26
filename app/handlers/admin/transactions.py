import html
import json
from datetime import datetime
from math import ceil
from typing import Dict, Optional

from aiogram import Dispatcher, types, F
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import (
    get_admin_transaction_view_keyboard,
    get_admin_transactions_keyboard,
)
from app.localization.texts import get_texts
from app.services.admin_transaction_service import AdminTransactionService
from app.services.payment_service import PaymentService
from app.utils.decorators import admin_required, error_handler
from app.utils.formatters import format_datetime


PER_PAGE = 10


def _parse_method_and_id(data: str, prefix: str) -> tuple[Optional[str], Optional[int], int]:
    parts = data.split("_")
    method: Optional[str] = None
    local_id: Optional[int] = None
    page = 1

    if len(parts) >= 5:
        method = parts[3]
        try:
            local_id = int(parts[4])
        except (TypeError, ValueError):
            local_id = None
        if len(parts) >= 6 and parts[5].startswith("p"):
            try:
                page = int(parts[5][1:])
            except (TypeError, ValueError):
                page = 1
    return method, local_id, page


def _method_display(texts, record, default: str = "") -> str:
    method = record.method
    payment_type = (record.payment_method_type or "").lower()

    if method == "yookassa":
        if payment_type == "sbp":
            return texts.t("ADMIN_TRANSACTIONS_METHOD_YOOKASSA_SBP", "🏦 YooKassa (СБП)")
        return texts.t("ADMIN_TRANSACTIONS_METHOD_YOOKASSA", "💳 YooKassa")
    if method == "mulenpay":
        name = settings.get_mulenpay_display_name()
        return texts.t(
            "ADMIN_TRANSACTIONS_METHOD_MULENPAY",
            f"💳 {name}",
        ).format(name=name)
    if method == "pal24":
        return texts.t("ADMIN_TRANSACTIONS_METHOD_PAL24", "🏦 PayPalych")
    if method == "wata":
        return texts.t("ADMIN_TRANSACTIONS_METHOD_WATA", "🏧 WATA")
    if method == "heleket":
        return texts.t("ADMIN_TRANSACTIONS_METHOD_HELEKET", "🪙 Heleket")
    if method == "cryptobot":
        return texts.t("ADMIN_TRANSACTIONS_METHOD_CRYPTOBOT", "🪙 CryptoBot")
    if method == "telegram_stars":
        return texts.t("ADMIN_TRANSACTIONS_METHOD_TELEGRAM_STARS", "⭐ Telegram Stars")
    if method == "tribute":
        return texts.t("ADMIN_TRANSACTIONS_METHOD_TRIBUTE", "💎 Tribute")
    if method == "manual":
        return texts.t("ADMIN_TRANSACTIONS_METHOD_MANUAL", "🛠️ Вручную")
    return default or method


def _status_display(texts, status: str) -> str:
    mapping: Dict[str, str] = {
        "pending": texts.t("ADMIN_TRANSACTIONS_STATUS_PENDING", "Ожидание"),
        "paid": texts.t("ADMIN_TRANSACTIONS_STATUS_PAID", "Оплачен"),
        "failed": texts.t("ADMIN_TRANSACTIONS_STATUS_FAILED", "Неудача"),
        "expired": texts.t("ADMIN_TRANSACTIONS_STATUS_EXPIRED", "Истёк"),
        "unknown": texts.t("ADMIN_TRANSACTIONS_STATUS_UNKNOWN", "Неизвестно"),
    }
    return mapping.get(status, mapping["unknown"])


def _format_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    return format_datetime(dt, "%d.%m.%Y %H:%M")


def _manual_check_reason(texts, reason: Optional[str]) -> str:
    if reason == "too_old":
        return texts.t("ADMIN_TRANSACTION_CHECK_TOO_OLD", "Платёж старше 24 часов.")
    if reason == "not_pending":
        return texts.t("ADMIN_TRANSACTION_CHECK_NOT_PENDING", "Платёж уже не в ожидании.")
    if reason == "unsupported":
        return texts.t(
            "ADMIN_TRANSACTION_CHECK_NOT_SUPPORTED",
            "Ручная проверка не поддерживается для этого метода.",
        )
    if reason == "not_found":
        return texts.t("ADMIN_TRANSACTION_CHECK_NOT_FOUND", "Платёж не найден или уже удалён.")
    if reason == "service_disabled":
        return texts.t(
            "ADMIN_TRANSACTION_CHECK_SERVICE_DISABLED",
            "Провайдер отключён, ручная проверка недоступна.",
        )
    return texts.t(
        "ADMIN_TRANSACTION_CHECK_NOT_SUPPORTED",
        "Ручная проверка не поддерживается для этого метода.",
    )


def _render_detail_text(texts, record, page: int, language: str) -> tuple[str, types.InlineKeyboardMarkup]:
    method_label = _method_display(texts, record)
    status_label = _status_display(texts, record.status)
    lines = [texts.t("ADMIN_TRANSACTION_DETAILS_TITLE", "💳 <b>Детали пополнения</b>")]

    user = record.user
    if user:
        full_name = html.escape(user.full_name or "—")
        username = f"@{html.escape(user.username)}" if getattr(user, "username", None) else "—"
        telegram_id = getattr(user, "telegram_id", "—")
        lines.append(f"👤 {full_name}")
        lines.append(f"🆔 Telegram ID: <code>{telegram_id}</code>")
        lines.append(f"📱 {username}")

    lines.append(f"💳 Метод: {method_label}")
    lines.append(f"📊 Статус: {record.status_emoji()} {status_label}")
    if record.status_raw and record.status_raw.lower() != record.status:
        lines.append(f"   (<code>{html.escape(record.status_raw)}</code>)")
    lines.append(f"💰 Сумма: {html.escape(record.amount_display)}")
    if record.amount_secondary and record.amount_secondary != record.amount_display:
        lines.append(f"   (~ {html.escape(record.amount_secondary)})")

    lines.append(f"📅 Создан: {_format_dt(record.created_at)}")
    if record.updated_at:
        lines.append(f"🔄 Обновлён: {_format_dt(record.updated_at)}")
    if record.paid_at:
        lines.append(f"✅ Оплачен: {_format_dt(record.paid_at)}")
    if record.expires_at:
        lines.append(f"⌛ Истекает: {_format_dt(record.expires_at)}")

    lines.append(f"🆔 Запись: <code>{record.local_id}</code>")
    if record.external_id:
        lines.append(f"🌐 Внешний ID: <code>{html.escape(str(record.external_id))}</code>")
    if record.transaction_id:
        lines.append(f"🧾 Транзакция: <code>{record.transaction_id}</code>")
    if record.description:
        lines.append(f"📝 {html.escape(record.description)}")
    if record.url:
        safe_url = html.escape(record.url)
        lines.append(f"🔗 <a href=\"{safe_url}\">Открыть ссылку оплаты</a>")

    if record.metadata:
        try:
            metadata_str = json.dumps(record.metadata, ensure_ascii=False)
        except Exception:
            metadata_str = str(record.metadata)
        if len(metadata_str) > 600:
            metadata_str = metadata_str[:597] + "..."
        lines.append(f"🧾 Данные: <code>{html.escape(metadata_str)}</code>")

    if record.callback_payload:
        try:
            payload_str = json.dumps(record.callback_payload, ensure_ascii=False)
        except Exception:
            payload_str = str(record.callback_payload)
        if len(payload_str) > 600:
            payload_str = payload_str[:597] + "..."
        lines.append(f"📦 Callback: <code>{html.escape(payload_str)}</code>")

    if record.can_manual_check:
        lines.append(
            texts.t(
                "ADMIN_TRANSACTION_DETAILS_READY_CHECK",
                "🔄 Можно выполнить ручную проверку.",
            )
        )
    else:
        lines.append(
            texts.t(
                "ADMIN_TRANSACTION_DETAILS_CANNOT_CHECK",
                "⚠️ Ручная проверка недоступна: {reason}",
            ).format(reason=_manual_check_reason(texts, record.manual_check_reason))
        )

    keyboard = get_admin_transaction_view_keyboard(
        language=language,
        method=record.method,
        local_id=record.local_id,
        page=page,
        can_manual_check=record.can_manual_check,
        user_id=getattr(record.user, "id", None),
    )

    return "\n".join(lines), keyboard


@admin_required
@error_handler
async def show_admin_transactions(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    data = callback.data or "admin_transactions"
    page = 1
    if data.startswith("admin_transactions_page_"):
        try:
            page = int(data.rsplit("_", maxsplit=1)[-1])
        except (TypeError, ValueError):
            page = 1

    if page < 1:
        page = 1

    texts = get_texts(db_user.language)
    service = AdminTransactionService()
    result = await service.list_payments(db, page=page, per_page=PER_PAGE)

    items = result["items"]
    total = result["total"]
    pending = result["pending"]
    total_pages = max(ceil(total / PER_PAGE), 1) if total else 1
    if page > total_pages:
        page = total_pages
        result = await service.list_payments(db, page=page, per_page=PER_PAGE)
        items = result["items"]
        total = result["total"]
        pending = result["pending"]

    header_lines = [texts.t("ADMIN_TRANSACTIONS_TITLE", "💳 <b>Пополнения пользователей</b>")]
    header_lines.append(
        texts.t(
            "ADMIN_TRANSACTIONS_SUMMARY",
            "📊 Всего: {total} · ⏳ В ожидании: {pending}\nСтраница {page}/{pages}",
        ).format(total=total, pending=pending, page=page, pages=total_pages)
    )

    rows: list[str] = []
    keyboard_items = []

    if not items:
        rows.append(texts.t("ADMIN_TRANSACTIONS_EMPTY", "📭 Пополнений пока нет."))
    else:
        for index, record in enumerate(items, start=(page - 1) * PER_PAGE + 1):
            method_label = _method_display(texts, record)
            status_label = _status_display(texts, record.status)
            user = record.user
            user_display = "—"
            if user:
                full_name = html.escape(user.full_name or "—")
                user_display = full_name
                if getattr(user, "telegram_id", None):
                    user_display += f" (ID: {user.telegram_id})"
            created_at = _format_dt(record.created_at)
            amount = record.amount_display
            if record.amount_secondary and record.amount_secondary != amount:
                amount = f"{amount} ({record.amount_secondary})"

            status_line = (
                f"{index}. {record.status_emoji()} {method_label} • {amount}\n"
                f"   {status_label} — {created_at}\n"
                f"   {user_display}"
            )
            if record.external_id:
                status_line += f"\n   ID: <code>{html.escape(str(record.external_id))}</code>"
            rows.append(status_line)

            keyboard_items.append(
                {
                    "text": f"{record.status_emoji()} {method_label} • {amount}",
                    "callback": f"admin_tx_view_{record.method}_{record.local_id}_p{page}",
                }
            )

    text = "\n\n".join(["\n".join(header_lines), "\n\n".join(rows)])

    keyboard = get_admin_transactions_keyboard(
        keyboard_items,
        current_page=page,
        total_pages=total_pages,
        language=db_user.language,
    )

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def view_admin_transaction(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    method, local_id, page = _parse_method_and_id(callback.data or "", "admin_tx_view")
    if not method or local_id is None:
        await callback.answer("❌ Некорректный идентификатор", show_alert=True)
        return

    texts = get_texts(db_user.language)
    service = AdminTransactionService()
    record = await service.get_payment_details(db, method, local_id)

    if not record:
        await callback.answer("❌ Платёж не найден", show_alert=True)
        return

    text, keyboard = _render_detail_text(texts, record, page, db_user.language)
    await callback.message.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)
    await callback.answer()


@admin_required
@error_handler
async def run_manual_check(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    method, local_id, page = _parse_method_and_id(callback.data or "", "admin_tx_check")
    if not method or local_id is None:
        await callback.answer("❌ Некорректный идентификатор", show_alert=True)
        return

    texts = get_texts(db_user.language)
    service = AdminTransactionService(payment_service=PaymentService(callback.bot))
    result = await service.run_manual_check(db, method, local_id)

    if not result.get("ok"):
        reason = result.get("error", "unsupported")
        await callback.answer(_manual_check_reason(texts, reason), show_alert=True)
        return

    updated = await service.get_payment_details(db, method, local_id)
    if not updated:
        await callback.answer("❌ Платёж не найден", show_alert=True)
        return

    text, keyboard = _render_detail_text(texts, updated, page, db_user.language)
    await callback.message.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)

    status_label = _status_display(texts, updated.status)
    await callback.answer(
        texts.t(
            "ADMIN_TRANSACTION_CHECK_SUCCESS",
            "Статус обновлён: {status}",
        ).format(status=status_label),
        show_alert=True,
    )


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(
        show_admin_transactions,
        F.data == "admin_transactions",
    )
    dp.callback_query.register(
        show_admin_transactions,
        F.data.startswith("admin_transactions_page_"),
    )
    dp.callback_query.register(
        view_admin_transaction,
        F.data.startswith("admin_tx_view_"),
    )
    dp.callback_query.register(
        run_manual_check,
        F.data.startswith("admin_tx_check_"),
    )
