"""Mixin, инкапсулирующий работу с платежами Wata Pay."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from importlib import import_module
from typing import Any, Dict, Optional

from dateutil import parser
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import PaymentMethod, TransactionType
from app.localization.texts import get_texts
from app.utils.user_utils import format_referrer_info

logger = logging.getLogger(__name__)


class WataPaymentMixin:
    """Mixin с созданием платежей, обработкой webhook и синхронизацией статусов Wata."""

    async def create_wata_payment(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        amount_kopeks: int,
        description: str,
        language: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        service = getattr(self, "wata_service", None)
        if not service or not service.is_configured:
            logger.error("Wata сервис не инициализирован")
            return None

        if amount_kopeks < settings.WATA_MIN_AMOUNT_KOPEKS:
            logger.warning(
                "Сумма Wata меньше минимальной: %s < %s",
                amount_kopeks,
                settings.WATA_MIN_AMOUNT_KOPEKS,
            )
            return None

        if amount_kopeks > settings.WATA_MAX_AMOUNT_KOPEKS:
            logger.warning(
                "Сумма Wata больше максимальной: %s > %s",
                amount_kopeks,
                settings.WATA_MAX_AMOUNT_KOPEKS,
            )
            return None

        order_id = f"wata_{user_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"

        payload = {
            "type": settings.WATA_LINK_TYPE or "OneTime",
            "amount": self._format_amount(amount_kopeks),
            "currency": settings.WATA_DEFAULT_CURRENCY or "RUB",
            "description": description,
            "orderId": order_id,
        }

        if settings.WATA_ALLOW_ARBITRARY_AMOUNT:
            payload["isArbitraryAmountAllowed"] = True

        if settings.WATA_SUCCESS_REDIRECT_URL:
            payload["successRedirectUrl"] = settings.WATA_SUCCESS_REDIRECT_URL
        if settings.WATA_FAIL_REDIRECT_URL:
            payload["failRedirectUrl"] = settings.WATA_FAIL_REDIRECT_URL

        try:
            response = await service.create_payment_link(**payload)
        except Exception as error:  # pragma: no cover - network failures
            logger.error("Ошибка Wata API при создании ссылки: %s", error)
            return None

        if not response:
            logger.error("Пустой ответ при создании Wata ссылки")
            return None

        payment_url = response.get("url")
        link_id = response.get("id")
        status = response.get("status", "Opened")

        if not payment_url:
            logger.error("Wata не вернул ссылку на оплату: %s", response)
            return None

        expiration_at = self._parse_datetime(response.get("expirationDateTime"))

        metadata = {
            "raw_response": response,
            "language": language or "ru",
        }

        payment_module = import_module("app.services.payment_service")
        payment = await payment_module.create_wata_payment(
            db,
            user_id=user_id,
            amount_kopeks=amount_kopeks,
            order_id=order_id,
            description=description,
            status=status,
            currency=response.get("currency", settings.WATA_DEFAULT_CURRENCY or "RUB"),
            payment_url=payment_url,
            wata_link_id=link_id,
            success_redirect_url=response.get("successRedirectUrl"),
            fail_redirect_url=response.get("failRedirectUrl"),
            expiration_at=expiration_at,
            metadata=metadata,
        )

        logger.info(
            "Создан Wata платеж %s для пользователя %s (%s₽)",
            order_id,
            user_id,
            amount_kopeks / 100,
        )

        return {
            "local_payment_id": payment.id,
            "order_id": order_id,
            "link_id": link_id,
            "payment_url": payment_url,
            "status": status,
            "amount_kopeks": amount_kopeks,
        }

    async def verify_wata_webhook_signature(
        self,
        raw_body: bytes,
        signature: str,
    ) -> bool:
        service = getattr(self, "wata_service", None)
        if not service or not service.is_configured:
            logger.error("Wata сервис не инициализирован для проверки подписи")
            return False

        try:
            return await service.verify_signature(raw_body, signature)
        except Exception as error:  # pragma: no cover - безопасность
            logger.error("Ошибка проверки подписи Wata: %s", error)
            return False

    async def process_wata_webhook(
        self,
        db: AsyncSession,
        payload: Dict[str, Any],
    ) -> bool:
        try:
            payment_module = import_module("app.services.payment_service")

            order_id = payload.get("orderId") or payload.get("order_id")
            link_id = payload.get("paymentLinkId") or payload.get("payment_link_id")
            transaction_status = payload.get("transactionStatus") or payload.get("transaction_status")
            transaction_id = payload.get("transactionId") or payload.get("transaction_id")
            amount_value = payload.get("amount")

            payment = None

            if order_id:
                payment = await payment_module.get_wata_payment_by_order_id(db, order_id)
            if not payment and link_id:
                payment = await payment_module.get_wata_payment_by_link_id(db, link_id)

            if not payment:
                logger.error(
                    "Wata платеж не найден (order_id=%s, link_id=%s)",
                    order_id,
                    link_id,
                )
                return False

            status = payload.get("status") or payment.status
            await payment_module.update_wata_payment_status(
                db,
                payment=payment,
                status=status,
                transaction_status=transaction_status,
                callback_payload=payload,
                external_transaction_id=transaction_id,
            )

            payment = await payment_module.get_wata_payment_by_local_id(db, payment.id)

            if payment.is_paid:
                logger.info("Wata платеж %s уже оплачен", payment.order_id)
                return True

            if (transaction_status or "").lower() == "paid":
                amount_kopeks = self._parse_amount_to_kopeks(amount_value)
                if amount_kopeks is None:
                    amount_kopeks = payment.amount_kopeks

                return await self._finalize_wata_payment(
                    db,
                    payment,
                    amount_kopeks=amount_kopeks,
                    transaction_id=transaction_id or payment.order_id,
                    transaction_payload=payload,
                )

            return True

        except Exception as error:
            logger.error("Ошибка обработки Wata webhook: %s", error, exc_info=True)
            return False

    async def get_wata_payment_status(
        self,
        db: AsyncSession,
        local_payment_id: int,
    ) -> Optional[Dict[str, Any]]:
        try:
            payment_module = import_module("app.services.payment_service")
            payment = await payment_module.get_wata_payment_by_local_id(db, local_payment_id)
            if not payment:
                return None

            service = getattr(self, "wata_service", None)
            remote_link: Optional[Dict[str, Any]] = None
            remote_transaction: Optional[Dict[str, Any]] = None

            if service and service.is_configured:
                if payment.wata_link_id:
                    remote_link = await service.get_payment_link(payment.wata_link_id)
                    if remote_link:
                        await payment_module.update_wata_payment_status(
                            db,
                            payment=payment,
                            status=remote_link.get("status", payment.status),
                            payment_url=remote_link.get("url", payment.payment_url),
                            last_status_payload=remote_link,
                        )
                        payment = await payment_module.get_wata_payment_by_local_id(db, local_payment_id)

                if payment.order_id:
                    try:
                        transactions_response = await service.search_transactions(order_id=payment.order_id)
                    except Exception as error:  # pragma: no cover - сеть
                        logger.error("Ошибка запроса транзакций Wata: %s", error)
                        transactions_response = None

                    if transactions_response:
                        items = transactions_response.get("items")
                        if isinstance(items, list):
                            for item in items:
                                if not isinstance(item, dict):
                                    continue
                                remote_transaction = item
                                status = (item.get("status") or "").lower()
                                if status == "paid" and not payment.is_paid:
                                    amount_kopeks = self._parse_amount_to_kopeks(item.get("amount"))
                                    if amount_kopeks is None:
                                        amount_kopeks = payment.amount_kopeks
                                    await self._finalize_wata_payment(
                                        db,
                                        payment,
                                        amount_kopeks=amount_kopeks,
                                        transaction_id=item.get("id") or payment.order_id,
                                        transaction_payload=item,
                                    )
                                    payment = await payment_module.get_wata_payment_by_local_id(
                                        db, local_payment_id
                                    )
                                    break

            return {
                "payment": payment,
                "remote_link": remote_link,
                "remote_transaction": remote_transaction,
            }

        except Exception as error:
            logger.error("Ошибка получения статуса Wata: %s", error, exc_info=True)
            return None

    async def _finalize_wata_payment(
        self,
        db: AsyncSession,
        payment: Any,
        *,
        amount_kopeks: int,
        transaction_id: str,
        transaction_payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        payment_module = import_module("app.services.payment_service")

        await payment_module.update_wata_payment_status(
            db,
            payment=payment,
            status="Closed",
            transaction_status="Paid",
            is_paid=True,
            paid_at=datetime.utcnow(),
            callback_payload=transaction_payload,
            external_transaction_id=transaction_id,
        )

        transaction = await payment_module.create_transaction(
            db=db,
            user_id=payment.user_id,
            type=TransactionType.DEPOSIT,
            amount_kopeks=amount_kopeks,
            description=f"Пополнение через Wata Pay: {payment.description or payment.order_id}",
            payment_method=PaymentMethod.WATA,
            external_id=transaction_id,
            metadata={"wata_payload": transaction_payload} if transaction_payload else None,
            is_completed=True,
        )

        await payment_module.link_wata_payment_to_transaction(
            db=db,
            payment=payment,
            transaction_id=transaction.id,
        )

        user = await payment_module.get_user_by_id(db, payment.user_id)
        if not user:
            logger.error("Пользователь %s не найден для Wata платежа", payment.user_id)
            return False

        old_balance = user.balance_kopeks
        was_first_topup = not user.has_made_first_topup

        user.balance_kopeks += amount_kopeks
        user.updated_at = datetime.utcnow()

        if was_first_topup:
            user.has_made_first_topup = True

        await db.commit()
        await db.refresh(user)

        try:
            from app.services.referral_service import process_referral_topup

            await process_referral_topup(
                db,
                user.id,
                amount_kopeks,
                getattr(self, "bot", None),
            )
        except Exception as error:
            logger.error("Ошибка обработки реферального пополнения Wata: %s", error)

        promo_group = getattr(user, "promo_group", None)
        subscription = getattr(user, "subscription", None)
        referrer_info = format_referrer_info(user)
        topup_status = "🆕 Первое пополнение" if was_first_topup else "🔄 Пополнение"

        if getattr(self, "bot", None):
            try:
                from app.services.admin_notification_service import (
                    AdminNotificationService,
                )

                notification_service = AdminNotificationService(self.bot)
                await notification_service.send_balance_topup_notification(
                    user,
                    transaction,
                    old_balance,
                    topup_status=topup_status,
                    referrer_info=referrer_info,
                    subscription=subscription,
                    promo_group=promo_group,
                    db=db,
                )
            except Exception as error:
                logger.error("Ошибка отправки уведомления о пополнении Wata: %s", error)

        if getattr(self, "bot", None) and user.telegram_id:
            try:
                texts = get_texts(getattr(user, "language", settings.DEFAULT_LANGUAGE))
                payment_method_title = texts.t(
                    "PAYMENT_CARD_WATA",
                    "🌐 Банковская карта (Wata Pay)",
                )
                await self._send_payment_success_notification(
                    user.telegram_id,
                    amount_kopeks,
                    user=user,
                    db=db,
                    payment_method_title=payment_method_title,
                )
            except Exception as error:
                logger.error("Ошибка отправки уведомления пользователю Wata: %s", error)

        logger.info(
            "✅ Обработан Wata платеж %s для пользователя %s",
            payment.order_id,
            payment.user_id,
        )
        return True

    def _format_amount(self, amount_kopeks: int) -> str:
        return f"{Decimal(amount_kopeks) / Decimal(100):.2f}"

    def _parse_amount_to_kopeks(self, amount: Any) -> Optional[int]:
        if amount is None:
            return None
        try:
            if isinstance(amount, (int, float, Decimal)):
                decimal_amount = Decimal(str(amount))
            elif isinstance(amount, str):
                decimal_amount = Decimal(amount)
            else:
                decimal_amount = Decimal(json.dumps(amount))
            return int(decimal_amount * 100)
        except (InvalidOperation, TypeError, ValueError):
            return None

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            return parser.isoparse(value)
        except (ValueError, TypeError, AttributeError):  # pragma: no cover - формат
            return None
