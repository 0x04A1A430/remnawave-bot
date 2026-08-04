"""Единый порядок удаления подписки: грейс-гард, автоплатежи, панель, строка.

Порядок шагов здесь не косметический, а выстраданный:

* грейс-гард берёт advisory-lock, поэтому проверка идёт первой — удалять
  подписку с открытым временным доступом нельзя;
* отмена автоплатежей идёт ДО удаления строки: записи платёжек уходят по
  CASCADE вместе с ней, и отменять потом было бы уже нечего. Отмена
  коммитит свою транзакцию и тем самым отпускает advisory-lock — поэтому
  сразу за ней гард берётся повторно, закрывая окно перед необратимыми
  шагами;
* удаление юзера в панели помечается как намеренное, иначе прилетевший
  ``user.deleted`` вебхук своей чисткой заденет соседние живые подписки
  того же владельца.

Вызывающий сам решает, как отвечать на ``GraceAccessDeletionBlocked``:
кабинет отдаёт 409, массовые действия — пропуск с пояснением.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.subscription import decrement_subscription_server_counts
from app.database.models import Subscription

logger = structlog.get_logger(__name__)


async def delete_subscription_record(
    db: AsyncSession,
    subscription: Subscription,
    *,
    deleted_by: str,
) -> None:
    """Удалить подписку целиком. Грейс-гард пробрасывается наружу.

    ``deleted_by`` попадает в лог — по нему видно, кто инициировал:
    сам владелец из кабинета или администратор из карточки.
    """
    from app.services.grace_access_runtime import ensure_no_open_grace_for_subscriptions

    await ensure_no_open_grace_for_subscriptions(db, (subscription.id,))

    from app.services.payment.lava import cancel_lava_recurring_for_subscription_safe
    from app.services.payment.platega import cancel_platega_recurring_for_subscription_safe

    await cancel_platega_recurring_for_subscription_safe(db, subscription.id)
    await cancel_lava_recurring_for_subscription_safe(db, subscription.id)

    # Автоплатёжки закоммитили своё — advisory-lock отпущен, берём заново.
    await ensure_no_open_grace_for_subscriptions(db, (subscription.id,))

    if subscription.remnawave_id:
        try:
            from app.services.remnawave_webhook_service import RemnaWaveWebhookService
            from app.services.subscription_service import SubscriptionService

            RemnaWaveWebhookService.mark_intentional_panel_deletion(
                panel_user_ids=[subscription.remnawave_id]
            )
            await SubscriptionService().delete_remnawave_user(subscription.remnawave_id)
        except Exception as error:
            logger.warning('Failed to delete RemnaWave user on subscription delete', error=error)

    await decrement_subscription_server_counts(db, subscription)

    subscription_id = subscription.id
    tariff_id = subscription.tariff_id
    user_id = subscription.user_id

    await db.delete(subscription)
    await db.commit()

    logger.info(
        'Subscription deleted',
        subscription_id=subscription_id,
        user_id=user_id,
        tariff_id=tariff_id,
        deleted_by=deleted_by,
    )
