"""Mixin для интеграции с Cashera (api.cashera.cash, server-to-server)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from importlib import import_module
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import PaymentMethod, TransactionType
from app.services.cashera_service import cashera_service, normalize_payment_url
from app.utils.payment_logger import payment_logger as logger
from app.utils.user_utils import format_referrer_info


# Статус Cashera -> (внутренний статус, оплачен ли)
CASHERA_STATUS_MAP: dict[str, tuple[str, bool]] = {
    'pending': ('pending', False),
    'paid': ('success', True),
    'failed': ('failed', False),
    'expired': ('expired', False),
    'refunded': ('refunded', False),
    'chargeback': ('chargeback', False),
}

# Финальные неуспехи: повторный вебхук не должен «чинить» такой платёж.
CASHERA_TERMINAL_FAILURES = frozenset({'failed', 'expired', 'refunded', 'chargeback', 'amount_mismatch', 'error'})


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class CasheraPaymentMixin:
    """Mixin для работы с платежами Cashera."""

    async def create_cashera_payment(
        self,
        db: AsyncSession,
        *,
        user_id: int | None,
        amount_kopeks: int,
        description: str = 'Пополнение баланса',
        language: str = 'ru',
        payment_method_code: str | None = None,
        return_url: str | None = None,
        fail_url: str | None = None,
    ) -> dict[str, Any] | None:
        """Создаёт платёж Cashera и сохраняет его до ответа покупателю.

        ``payment_method_code`` — код метода Cashera (sbp, card, …) из активных.
        Без него создаётся общая платёжная форма, где метод выбирает покупатель.
        """
        if not settings.is_cashera_enabled():
            logger.error('Cashera не настроена')
            return None

        if amount_kopeks < settings.CASHERA_MIN_AMOUNT_KOPEKS or amount_kopeks > settings.CASHERA_MAX_AMOUNT_KOPEKS:
            logger.warning(
                'Cashera: сумма вне допустимого диапазона',
                amount_kopeks=amount_kopeks,
                min_kopeks=settings.CASHERA_MIN_AMOUNT_KOPEKS,
                max_kopeks=settings.CASHERA_MAX_AMOUNT_KOPEKS,
            )
            return None

        if payment_method_code is not None and payment_method_code not in settings.get_cashera_active_methods():
            logger.warning('Cashera: метод не включён', payment_method=payment_method_code)
            return None

        payment_module = import_module('app.services.payment_service')
        if user_id is not None:
            user = await payment_module.get_user_by_id(db, user_id)
            tg_id = user.telegram_id if user and user.telegram_id else f'u{user_id}'
        else:
            tg_id = 'guest'

        # external_id Cashera: буквы, цифры и . _ - (до 255). Он же ключ идемпотентности.
        order_id = f'cas{tg_id}_{uuid.uuid4().hex[:10]}'

        metadata = {
            'user_id': user_id,
            'amount_kopeks': amount_kopeks,
            'description': description,
            'language': language,
            'type': 'balance_topup',
            'payment_method': payment_method_code,
        }

        try:
            api_result = await cashera_service.create_transaction(
                amount_kopeks=amount_kopeks,
                external_id=order_id,
                description=description,
                payment_method=payment_method_code,
                callback_url=settings.get_cashera_callback_url(),
                success_url=return_url or settings.get_cashera_return_url(),
                fail_url=fail_url or settings.get_cashera_failed_url(),
            )
        except Exception as error:
            logger.exception('Cashera: ошибка создания платежа', error=error)
            return None

        cashera_uuid = str(api_result.get('uuid'))
        payment_url = normalize_payment_url(api_result.get('payment_url'))
        expires_at = _parse_datetime(api_result.get('expires_at'))

        cashera_crud = import_module('app.database.crud.cashera')
        # Сохраняем даже без payment_url: транзакция на стороне Cashera создана, и
        # пришедший вебхук должен найти платёж, иначе деньги пришлось бы сверять руками.
        local_payment = await cashera_crud.create_cashera_payment(
            db=db,
            user_id=user_id,
            order_id=order_id,
            amount_kopeks=amount_kopeks,
            currency='RUB',
            description=description,
            payment_url=payment_url,
            payment_method=api_result.get('payment_method') or payment_method_code,
            cashera_uuid=cashera_uuid,
            cashera_status=(api_result.get('status') or 'pending').lower(),
            expires_at=expires_at,
            metadata_json=metadata,
        )

        if not payment_url:
            logger.warning('Cashera: ответ без payment_url', order_id=order_id, cashera_uuid=cashera_uuid)

        logger.info(
            'Cashera: создан платеж',
            order_id=order_id,
            user_id=user_id,
            amount_kopeks=amount_kopeks,
            payment_method=payment_method_code,
        )

        return {
            'order_id': order_id,
            'amount_kopeks': amount_kopeks,
            'amount_rubles': amount_kopeks / 100,
            'currency': 'RUB',
            'payment_url': payment_url,
            'payment_id': cashera_uuid,
            'expires_at': expires_at.isoformat() if expires_at else None,
            'local_payment_id': local_payment.id,
        }

    async def process_cashera_webhook(self, db: AsyncSession, payload: dict[str, Any]) -> bool:
        """Обрабатывает вебхук Cashera (подлинность уже проверена в webserver).

        True — принять (ответ 2xx). False — ответить 5xx, чтобы Cashera повторила:
        только там, где повтор может помочь. 4xx Cashera не повторяет вовсе.
        """
        event = payload.get('event')
        if event != 'transaction.status_updated':
            # webhook.test, выплаты, подписки и будущие события — просто подтверждаем.
            logger.info('Cashera webhook: событие не про платёж, подтверждаем', cashera_event=event)
            return True

        transaction = payload.get('transaction')
        if not isinstance(transaction, dict):
            logger.warning('Cashera webhook: нет объекта transaction')
            return True

        order_id = transaction.get('external_id')
        if not order_id:
            logger.warning('Cashera webhook: нет external_id', cashera_uuid=transaction.get('uuid'))
            return True

        try:
            cashera_crud = import_module('app.database.crud.cashera')
            payment = await cashera_crud.get_cashera_payment_by_order_id(db, str(order_id))
            if not payment:
                # Не наш платёж (например, другой интеграции того же мерчанта) — повтор не поможет.
                logger.warning('Cashera webhook: платеж не найден', order_id=order_id)
                return True

            locked = await cashera_crud.get_cashera_payment_by_id_for_update(db, payment.id)
            if not locked:
                logger.error('Cashera: не удалось заблокировать платёж', payment_id=payment.id)
                return False

            return await self._apply_cashera_transaction(db, locked, transaction, source='webhook')
        except Exception as error:
            logger.exception('Cashera webhook: ошибка обработки', error=error)
            return False

    async def _apply_cashera_transaction(
        self,
        db: AsyncSession,
        payment: Any,
        transaction: dict[str, Any],
        *,
        source: str,
    ) -> bool:
        """Применяет состояние транзакции Cashera к платежу (FOR UPDATE уже взят).

        Общая логика вебхука и сверки через API. Возвращает False только когда
        имеет смысл повторить (подтверждённой суммы в paid нет).
        """
        cashera_crud = import_module('app.database.crud.cashera')
        incoming_status = str(transaction.get('status') or '').strip().lower()
        callback_payload = {
            'source': source,
            'uuid': transaction.get('uuid'),
            'status': incoming_status,
            'amount': transaction.get('amount'),
            'gross_amount': transaction.get('gross_amount'),
            'net_amount': transaction.get('net_amount'),
            'currency': transaction.get('currency'),
            'payment_method': transaction.get('payment_method'),
            'paid_at': transaction.get('paid_at'),
        }

        if payment.is_paid:
            if incoming_status in {'refunded', 'chargeback'} and payment.cashera_status != incoming_status:
                # Деньги уже зачислены — автоматически не списываем, но оставляем след и тревогу.
                logger.error(
                    'Cashera: возврат/чарджбэк по уже зачисленному платежу — разобрать вручную',
                    order_id=payment.order_id,
                    user_id=payment.user_id,
                    amount_kopeks=payment.amount_kopeks,
                    cashera_status=incoming_status,
                )
                payment.cashera_status = incoming_status
                payment.callback_payload = callback_payload
                payment.updated_at = datetime.now(UTC)
                await db.commit()
            else:
                logger.info('Cashera: платеж уже обработан', order_id=payment.order_id, source=source)
            return True

        if payment.status in CASHERA_TERMINAL_FAILURES:
            logger.warning(
                'Cashera: платёж в финальном неуспешном статусе, событие игнорируется',
                order_id=payment.order_id,
                current_status=payment.status,
                incoming_status=incoming_status,
            )
            return True

        if incoming_status and incoming_status == payment.cashera_status and incoming_status != 'paid':
            # Идемпотентность uuid + status: этот статус уже обработан.
            return True

        if incoming_status not in CASHERA_STATUS_MAP:
            logger.warning('Cashera: неизвестный статус', order_id=payment.order_id, cashera_status=incoming_status)
            return True

        internal_status, is_paid = CASHERA_STATUS_MAP[incoming_status]
        transaction_uuid = transaction.get('uuid')

        if is_paid:
            received_amount = transaction.get('amount')
            if received_amount is None:
                # Без подтверждённой суммы не зачисляем; статус не финальный — повтор или
                # сверка через API ещё могут закрыть платёж.
                logger.error('Cashera: paid без поля amount, зачисление отменено', order_id=payment.order_id)
                return False
            try:
                received_kopeks = int(received_amount)
            except (TypeError, ValueError):
                received_kopeks = None
            currency = str(transaction.get('currency') or '').upper()

            if received_kopeks != payment.amount_kopeks or currency != (payment.currency or 'RUB').upper():
                logger.error(
                    'Cashera amount mismatch',
                    order_id=payment.order_id,
                    expected_kopeks=payment.amount_kopeks,
                    received_amount=received_amount,
                    expected_currency=payment.currency,
                    received_currency=currency,
                )
                await cashera_crud.update_cashera_payment_status(
                    db=db,
                    payment=payment,
                    status='amount_mismatch',
                    is_paid=False,
                    cashera_status=incoming_status,
                    callback_payload=callback_payload,
                )
                # Повтор не исправит расхождение — подтверждаем, платёж ждёт разбора.
                return True

            payment.status = internal_status
            payment.is_paid = True
            payment.cashera_status = incoming_status
            payment.paid_at = _parse_datetime(transaction.get('paid_at')) or datetime.now(UTC)
            if transaction_uuid:
                payment.cashera_uuid = str(transaction_uuid)
            if transaction.get('payment_method'):
                payment.payment_method = transaction.get('payment_method')
            payment.callback_payload = callback_payload
            payment.updated_at = datetime.now(UTC)
            # Без промежуточного commit — он снял бы FOR UPDATE lock до зачисления.
            await db.flush()
            return await self._finalize_cashera_payment(db, payment, trigger=source)

        await cashera_crud.update_cashera_payment_status(
            db=db,
            payment=payment,
            status=internal_status,
            is_paid=False,
            cashera_status=incoming_status,
            cashera_uuid=str(transaction_uuid) if transaction_uuid else None,
            callback_payload=callback_payload,
        )
        return True

    async def _finalize_cashera_payment(self, db: AsyncSession, payment: Any, *, trigger: str) -> bool:
        """Создаёт транзакцию, начисляет баланс и отправляет уведомления.

        FOR UPDATE lock уже взят вызывающим.
        """
        payment_module = import_module('app.services.payment_service')
        cashera_crud = import_module('app.database.crud.cashera')

        if payment.transaction_id:
            logger.info(
                'Cashera платеж уже связан с транзакцией',
                order_id=payment.order_id,
                transaction_id=payment.transaction_id,
                trigger=trigger,
            )
            await db.commit()
            return True

        metadata = dict(getattr(payment, 'metadata_json', {}) or {})

        from app.services.payment.common import try_fulfill_guest_purchase

        guest_result = await try_fulfill_guest_purchase(
            db,
            metadata=metadata,
            payment_amount_kopeks=payment.amount_kopeks,
            provider_payment_id=payment.order_id,
            provider_name='cashera',
        )
        if guest_result is not None:
            return True

        balance_already_credited = bool(metadata.get('balance_credited'))

        user = await payment_module.get_user_by_id(db, payment.user_id)
        if not user:
            logger.error('Пользователь не найден для Cashera', user_id=payment.user_id)
            return False

        await db.refresh(user, attribute_names=['promo_group', 'user_promo_groups'])
        for user_promo_group in getattr(user, 'user_promo_groups', []):
            await db.refresh(user_promo_group, attribute_names=['promo_group'])

        promo_group = user.get_primary_promo_group()
        subscription = getattr(user, 'subscription', None)
        referrer_info = format_referrer_info(user)

        transaction_external_id = payment.order_id
        existing_transaction = await payment_module.get_transaction_by_external_id(
            db,
            transaction_external_id,
            PaymentMethod.CASHERA,
        )

        display_name = settings.get_cashera_display_name()
        description = f'Пополнение через {display_name}'

        transaction = existing_transaction
        created_transaction = False
        if not transaction:
            transaction = await payment_module.create_transaction(
                db,
                user_id=payment.user_id,
                type=TransactionType.DEPOSIT,
                amount_kopeks=payment.amount_kopeks,
                description=description,
                payment_method=PaymentMethod.CASHERA,
                external_id=transaction_external_id,
                is_completed=True,
                created_at=getattr(payment, 'created_at', None),
                commit=False,
            )
            created_transaction = True

        await cashera_crud.link_cashera_payment_to_transaction(db, payment=payment, transaction_id=transaction.id)

        if not (created_transaction or not balance_already_credited):
            logger.info('Cashera платеж уже зачислил баланс ранее', order_id=payment.order_id)
            await db.commit()
            return True

        from app.database.crud.user import lock_user_for_update

        user = await lock_user_for_update(db, user)

        old_balance = user.balance_kopeks
        was_first_topup = not user.has_made_first_topup

        user.balance_kopeks += payment.amount_kopeks
        user.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(user)

        from app.database.crud.transaction import emit_transaction_side_effects

        await emit_transaction_side_effects(
            db,
            transaction,
            amount_kopeks=payment.amount_kopeks,
            user_id=payment.user_id,
            type=TransactionType.DEPOSIT,
            payment_method=PaymentMethod.CASHERA,
            external_id=transaction_external_id,
        )

        topup_status = '\U0001f195 Первое пополнение' if was_first_topup else '\U0001f504 Пополнение'

        try:
            from app.services.referral_service import process_referral_topup

            await process_referral_topup(db, user.id, payment.amount_kopeks, getattr(self, 'bot', None))
        except Exception as error:
            logger.error('Ошибка обработки реферального пополнения Cashera', error=error)

        if was_first_topup and not user.has_made_first_topup and not user.referred_by_id:
            user.has_made_first_topup = True
            await db.commit()
            await db.refresh(user)

        if getattr(self, 'bot', None):
            try:
                from app.services.admin_notification_service import AdminNotificationService

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
                logger.error('Ошибка отправки админ уведомления Cashera', error=error)

        if getattr(self, 'bot', None) and user.telegram_id and settings.is_notifications_enabled():
            try:
                keyboard = await self.build_topup_success_keyboard(user)
                await self.bot.send_message(
                    user.telegram_id,
                    (
                        '✅ <b>Пополнение успешно!</b>\n\n'
                        f'\U0001f4b0 Сумма: {settings.format_price(payment.amount_kopeks)}\n'
                        f'\U0001f4b3 Способ: {settings.get_cashera_display_name_html()}\n'
                        f'\U0001f194 Транзакция: {transaction.id}\n\n'
                        'Баланс пополнен автоматически!'
                    ),
                    parse_mode='HTML',
                    reply_markup=keyboard,
                )
            except Exception as error:
                logger.error('Ошибка отправки уведомления пользователю Cashera', error=error)

        try:
            from app.services.payment.common import send_cart_notification_after_topup

            await send_cart_notification_after_topup(user, payment.amount_kopeks, db, getattr(self, 'bot', None))
        except Exception as error:
            logger.error(
                'Ошибка при работе с сохраненной корзиной для пользователя',
                user_id=payment.user_id,
                error=error,
                exc_info=True,
            )

        metadata['balance_change'] = {
            'old_balance': old_balance,
            'new_balance': user.balance_kopeks,
            'credited_at': datetime.now(UTC).isoformat(),
        }
        metadata['balance_credited'] = True
        payment.metadata_json = metadata
        await db.commit()

        logger.info('Обработан Cashera платеж', order_id=payment.order_id, user_id=payment.user_id, trigger=trigger)
        return True

    async def check_cashera_payment_status(self, db: AsyncSession, order_id: str) -> dict[str, Any] | None:
        """Сверяет платёж с Cashera через API и синхронизирует БД.

        Резерв на случай, если вебхук не дошёл: ручная проверка из админки,
        кнопка «Проверить статус» и фоновая сверка.
        """
        cashera_crud = import_module('app.database.crud.cashera')
        payment = await cashera_crud.get_cashera_payment_by_order_id(db, order_id)
        if not payment:
            logger.warning('Cashera payment not found', order_id=order_id)
            return None

        if payment.is_paid or payment.status in CASHERA_TERMINAL_FAILURES:
            return {'payment': payment, 'status': payment.status, 'is_paid': bool(payment.is_paid)}

        try:
            if payment.cashera_uuid:
                remote = await cashera_service.get_transaction(payment.cashera_uuid)
            else:
                remote = await cashera_service.get_transaction_by_external_id(payment.order_id)
        except Exception as error:
            logger.error('Cashera: не удалось получить статус через API', order_id=order_id, error=str(error))
            return {'payment': payment, 'status': payment.status or 'pending', 'is_paid': bool(payment.is_paid)}

        locked = await cashera_crud.get_cashera_payment_by_id_for_update(db, payment.id)
        if not locked:
            logger.error('Cashera: не удалось заблокировать платёж', payment_id=payment.id)
            return None

        await self._apply_cashera_transaction(db, locked, remote, source='api_check')
        await db.refresh(locked)
        return {'payment': locked, 'status': locked.status or 'pending', 'is_paid': bool(locked.is_paid)}

    async def get_cashera_payment_status(self, db: AsyncSession, local_payment_id: int) -> dict[str, Any] | None:
        """Статус по локальному id — для кнопки «Проверить статус» в боте."""
        cashera_crud = import_module('app.database.crud.cashera')
        payment = await cashera_crud.get_cashera_payment_by_id(db, local_payment_id)
        if not payment:
            return None
        return await self.check_cashera_payment_status(db, payment.order_id)
