from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.localization.texts import get_texts


if TYPE_CHECKING:
    from app.database.models import Subscription, Tariff

# Премиум (кастомные) эмодзи из набора проекта — как в inline_gift / locales.
DEVICES_EMOJI_HTML = "<tg-emoji emoji-id='5877318502947229960'>💻</tg-emoji>"
TRAFFIC_EMOJI_HTML = "<tg-emoji emoji-id='5931472654660800739'>📊</tg-emoji>"
MONEY_EMOJI_HTML = "<tg-emoji emoji-id='5769403330761593044'>💰</tg-emoji>"


@dataclass
class SubscriptionDeviation:
    """Расхождение подписки пользователя со стандартным тарифом/настройками."""

    extra_devices: int = 0
    extra_traffic_gb: int = 0
    extra_cost_kopeks: int = 0  # доплата за эти опции в текущем счёте (со скидками)

    def has(self) -> bool:
        return bool(self.extra_devices or self.extra_traffic_gb)


def compute_tariff_deviation(
    subscription: Subscription | None,
    tariff: Tariff,
    *,
    extra_cost_kopeks: int = 0,
) -> SubscriptionDeviation:
    """Отличия подписки от стандартных параметров тарифа (тарифный режим)."""
    deviation = SubscriptionDeviation(extra_cost_kopeks=max(0, extra_cost_kopeks))
    if subscription is None:
        return deviation

    tariff_traffic = tariff.traffic_limit_gb or 0
    if tariff_traffic and (subscription.traffic_limit_gb or 0) > tariff_traffic:
        deviation.extra_traffic_gb = (subscription.traffic_limit_gb or 0) - tariff_traffic

    extra_devices = max(0, (subscription.device_limit or 0) - (tariff.device_limit or 0))
    deviation.extra_devices = extra_devices
    return deviation


def compute_classic_deviation(
    subscription: Subscription | None,
    *,
    extra_cost_kopeks: int = 0,
) -> SubscriptionDeviation:
    """Отличия подписки от стандартной конфигурации (классический режим)."""
    deviation = SubscriptionDeviation(extra_cost_kopeks=max(0, extra_cost_kopeks))
    if subscription is None:
        return deviation

    deviation.extra_traffic_gb = subscription.purchased_traffic_gb or 0
    default_devices = settings.DEFAULT_DEVICE_LIMIT
    deviation.extra_devices = max(0, (subscription.device_limit or 0) - default_devices)
    if not deviation.has() and (subscription.traffic_limit_gb or 0) > settings.DEFAULT_TRAFFIC_LIMIT_GB:
        deviation.extra_traffic_gb = (subscription.traffic_limit_gb or 0) - settings.DEFAULT_TRAFFIC_LIMIT_GB
    return deviation


async def compute_renewal_deviation(
    subscription: Subscription,
    db=None,
    *,
    period_days: int = 30,
) -> SubscriptionDeviation:
    """Автоматический выбор режима по наличию тарифа у подписки."""
    from app.database.crud.tariff import get_tariff_by_id

    extra_cost = 0
    if db is not None:
        try:
            from app.services.pricing_engine import pricing_engine

            pricing = await pricing_engine.calculate_renewal_price(db, subscription, period_days)
            extra_cost = max(0, pricing.devices_price) + max(0, pricing.traffic_price)
        except Exception:
            extra_cost = 0

    if subscription.tariff_id:
        tariff = subscription.tariff or await get_tariff_by_id(db, subscription.tariff_id)
        if tariff is not None:
            return compute_tariff_deviation(subscription, tariff, extra_cost_kopeks=extra_cost)
    return compute_classic_deviation(subscription, extra_cost_kopeks=extra_cost)


def build_deviation_lines(deviation: SubscriptionDeviation, texts=None) -> list[str]:
    """Строки «+2 устройства», «+50 ГБ (докупленный трафик)»."""
    if not deviation.has():
        return []
    texts = texts or get_texts('ru')
    lines: list[str] = []
    if deviation.extra_devices:
        n = deviation.extra_devices
        if n % 10 == 1 and n % 100 != 11:
            devices_word = texts.t('DEVIATION_DEVICE_ONE', 'доп. устройство')
        elif 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20):
            devices_word = texts.t('DEVIATION_DEVICE_FEW', 'доп. устройства')
        else:
            devices_word = texts.t('DEVIATION_DEVICE_MANY', 'доп. устройств')
        lines.append(
            texts.t('DEVIATION_EXTRA_DEVICES', f'{DEVICES_EMOJI_HTML} +{{count}} {{word}}').format(count=n, word=devices_word)
        )
    if deviation.extra_traffic_gb:
        lines.append(
            texts.t(
                'DEVIATION_EXTRA_TRAFFIC',
                f'{TRAFFIC_EMOJI_HTML} +{{gb}} ГБ трафика (докупленный)',
            ).format(gb=deviation.extra_traffic_gb)
        )
    return lines


def build_deviation_text(deviation: SubscriptionDeviation, texts=None) -> str:
    """Готовый HTML-блок, объясняющий почему счёт дороже стандартной цены."""
    lines = build_deviation_lines(deviation, texts)
    if not lines:
        return ''
    texts = texts or get_texts('ru')
    body = [texts.t('DEVIATION_TITLE', '<b>Стоимость выше стандартной тарифа:</b>')]
    body.extend(lines)
    if deviation.extra_cost_kopeks > 0:
        body.append(
            texts.t(
                'DEVIATION_EXTRA_COST',
                f'{MONEY_EMOJI_HTML} Доплата за доп. опции: {{amount}}',
            ).format(amount=texts.format_price(deviation.extra_cost_kopeks))
        )
    return '\n'.join(body)


def build_deviation_button_row(markup, texts=None):
    """Добавляет к клавиатуре кнопку «Написать в поддержку» (без эмодзи)."""
    texts = texts or get_texts('ru')
    support_url = settings.get_support_contact_url()
    if not support_url:
        return markup
    try:
        button = InlineKeyboardButton(
            text=texts.t('CONTACT_SUPPORT_BUTTON', 'Написать в поддержку'),
            url=support_url,
        )
        rows = list(markup.inline_keyboard)
        if rows and any(btn.url == support_url for btn in rows[-1] if getattr(btn, 'url', None)):
            return markup
        return InlineKeyboardMarkup(inline_keyboard=[*rows[:-1], [button], rows[-1]])
    except Exception:
        return markup
