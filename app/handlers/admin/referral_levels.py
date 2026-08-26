"""Админский редактор уровней реферальных наград.

Отдельный модуль, а не ещё одна секция в ``referrals.py``: тот уже на полторы
тысячи строк и держит статистику, диагностику и заявки на вывод.

Что здесь настраивается на каждом уровне цепочки:

* **какие бонусы активны** — деньги, дни подписки или оба сразу;
* **повод** — регистрация, первое пополнение или каждое пополнение;
* **сколько получает пригласивший** — процент от суммы и/или фиксированная
  сумма и/или дни подписки в конкретном тарифе;
* **сколько получает приглашённый** — фиксированная сумма и/или дни;
* **лимит оплаченных комиссий** для пары.

Правила живут в таблице, а не в ``Settings``. Причина практическая: ключ,
прописанный в ``.env``, попадает в ``ENV_OVERRIDE_KEYS`` и перестаёт меняться из
админки — запись ложится в БД и не применяется. Реферальная секция на типовой
установке залочена именно так, и складывать туда ещё десяток ключей на уровень
значило бы повторить ту же ловушку.
"""

from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.referral_reward_level import (
    MAX_SUPPORTED_LEVEL,
    delete_reward_level,
    get_all_reward_levels,
    get_reward_level,
    upsert_reward_level,
)
from app.database.crud.tariff import get_all_tariffs
from app.database.models import ReferralRewardMode, ReferralRewardTrigger, User
from app.services.system_settings_service import bot_configuration_service
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler


_MODE_LABELS = {
    ReferralRewardMode.MONEY.value: '💰 Только деньги',
    ReferralRewardMode.DAYS.value: '📅 Только дни',
    ReferralRewardMode.BOTH.value: '💰📅 Деньги и дни',
}

_TRIGGER_LABELS = {
    ReferralRewardTrigger.REGISTRATION.value: '👥 За регистрацию',
    ReferralRewardTrigger.FIRST_TOPUP.value: '🎉 За первое пополнение',
    ReferralRewardTrigger.EVERY_TOPUP.value: '🔁 С каждого пополнения',
}

# Порядок перебора по кругу: одна кнопка вместо подменю на два пункта.
_MODE_CYCLE = [ReferralRewardMode.MONEY.value, ReferralRewardMode.DAYS.value, ReferralRewardMode.BOTH.value]
_TRIGGER_CYCLE = [
    ReferralRewardTrigger.REGISTRATION.value,
    ReferralRewardTrigger.FIRST_TOPUP.value,
    ReferralRewardTrigger.EVERY_TOPUP.value,
]

# Поля, которые правятся вводом числа: подпись, единица, максимум.
_NUMERIC_FIELDS = {
    'referrer_percent': ('Процент пригласившему', '%', 100),
    'referrer_fixed_kopeks': ('Фикс. сумма пригласившему', '₽', None),
    'referrer_days': ('Дни пригласившему', 'дн.', 3650),
    'referee_fixed_kopeks': ('Фикс. сумма приглашённому', '₽', None),
    'referee_days': ('Дни приглашённому', 'дн.', 3650),
    'max_payments': ('Лимит оплаченных комиссий (0 = без лимита)', 'шт.', None),
}

_MONEY_FIELDS = frozenset({'referrer_fixed_kopeks', 'referee_fixed_kopeks'})

# Сколько тарифов помещается в одно сообщение с кнопками. Превышение не молчит:
# «показаны первые N из M» честнее, чем список, из которого тариф просто исчез.
_TARIFF_PICKER_LIMIT = 40


def _fmt_optional_percent(value: int | None) -> str:
    """Пустой процент — это ноль, и так и надо писать.

    Показывать «—» было бы двусмысленно: админ прочитал бы это как «берётся
    откуда-то ещё», хотя откат к глобальному ``REFERRAL_COMMISSION_PERCENT``
    убран намеренно.
    """
    return f'{value}%' if value else 'не начисляется'


def _fmt_optional_money(value: int | None) -> str:
    return settings.format_price(value) if value else 'не начисляется'


def _fmt_days(days: int, tariff_name: str | None) -> str:
    if not days:
        return 'не начисляются'
    suffix = f' → {tariff_name}' if tariff_name else ' → основная подписка'
    return f'{days} дн.{suffix}'


async def _tariff_names(db: AsyncSession) -> dict[int, str]:
    tariffs = await get_all_tariffs(db, include_inactive=True)
    return {tariff.id: tariff.name for tariff in tariffs}


def _scheme_line() -> str:
    if settings.is_referral_levels_scheme():
        return f'✅ Многоуровневая схема включена (глубина: до {settings.get_referral_max_level_depth()})'
    return '⚠️ Схема наград: классическая — уровни ниже НЕ применяются'


async def _render_levels(callback: types.CallbackQuery, db: AsyncSession) -> None:
    """Отрисовать список уровней. Намеренно БЕЗ ``callback.answer()``.

    На один callback Telegram принимает ровно один ответ. Хендлеры, которые
    сначала подтверждают действие своим текстом, а потом перерисовывают экран,
    иначе отвечали бы дважды — второй вызов падает с «query is invalid».
    """
    levels = await get_all_reward_levels(db)
    names = await _tariff_names(db)

    lines = ['🪜 <b>Уровни реферальных наград</b>', '', _scheme_line(), '']

    if not levels:
        lines.append('Уровни не заведены — награды по этой схеме не начисляются.')
    else:
        for level in levels:
            status = '✅' if level.is_active else '⛔️'
            lines.append(
                f'{status} <b>Уровень {level.level}</b> — {_MODE_LABELS.get(level.reward_mode, level.reward_mode)}'
            )
            lines.append(f'   Повод: {_TRIGGER_LABELS.get(level.trigger, level.trigger)}')

            referrer_parts = []
            if level.reward_mode in (ReferralRewardMode.MONEY.value, ReferralRewardMode.BOTH.value):
                if level.referrer_percent:
                    referrer_parts.append(f'{level.referrer_percent}%')
                if level.referrer_fixed_kopeks:
                    referrer_parts.append(settings.format_price(level.referrer_fixed_kopeks))
            if level.reward_mode in (ReferralRewardMode.DAYS.value, ReferralRewardMode.BOTH.value):
                if level.referrer_days:
                    referrer_parts.append(_fmt_days(level.referrer_days, names.get(level.referrer_tariff_id)))
            lines.append(f'   Пригласившему: {" + ".join(referrer_parts) or "ничего"}')

            referee_parts = []
            if level.reward_mode in (ReferralRewardMode.MONEY.value, ReferralRewardMode.BOTH.value):
                if level.referee_fixed_kopeks:
                    referee_parts.append(settings.format_price(level.referee_fixed_kopeks))
            if level.reward_mode in (ReferralRewardMode.DAYS.value, ReferralRewardMode.BOTH.value):
                if level.referee_days:
                    referee_parts.append(_fmt_days(level.referee_days, names.get(level.referee_tariff_id)))
            # Показывается только когда есть что показать: у большинства правил
            # приглашённому не платят, и пустая строка была бы шумом. Но правило
            # «только приглашённому» без неё читалось как «не платит ничего».
            if referee_parts:
                lines.append(f'   Приглашённому: {" + ".join(referee_parts)}')

            lines.append('')

    lines.append(
        '<i>Правила хранятся в базе, а не в .env, поэтому меняются отсюда и из кабинета и переживают перезапуск.</i>'
    )

    max_depth = settings.get_referral_max_level_depth()
    keyboard_rows = []
    for level in levels:
        # Уровень глубже предела обхода не платит вовсе: помечаем прямо на кнопке,
        # иначе «✅ Уровень 4» неотличим от работающего.
        mark = '✅' if level.is_active else '⛔️'
        suffix = ' (не платит)' if level.level > max_depth else ''
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text=f'{mark} Уровень {level.level}{suffix}', callback_data=f'admin_ref_lvl:{level.level}'
                )
            ]
        )

    next_level = _next_free_level(levels)
    if next_level <= MAX_SUPPORTED_LEVEL:
        keyboard_rows.append(
            [types.InlineKeyboardButton(text=f'➕ Добавить уровень {next_level}', callback_data='admin_ref_lvl_add')]
        )

    if not levels:
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text='📥 Перенести текущие настройки в уровень 1', callback_data='admin_ref_lvl_import'
                )
            ]
        )

    scheme_toggle = '🔻 Вернуть классическую' if settings.is_referral_levels_scheme() else '🔺 Включить многоуровневую'
    keyboard_rows.append([types.InlineKeyboardButton(text=scheme_toggle, callback_data='admin_ref_lvl_scheme')])
    keyboard_rows.append([types.InlineKeyboardButton(text='⬅️ Назад', callback_data='admin_referrals_settings')])

    await callback.message.edit_text(
        '\n'.join(lines), reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    )


async def _cancel_pending_input(state: FSMContext | None) -> None:
    """Снять ожидание ввода значения при возврате на любой экран уровней.

    «Отмена» в редакторе поля ведёт на карточку уровня, а состояние оставалось
    взведённым: следующее произвольное сообщение админа в чат попадало в
    ``process_level_value`` и переписывало денежное поле. Набранное позже «100»
    превращалось в «процент пригласившему = 100%» без единого вопроса.

    Глобальный фоллбек неизвестных сообщений сюда не помогает: он навешен с
    ``StateFilter(None)`` и такое сообщение не перехватывает.
    """
    if state is None:
        return
    if await state.get_state() == AdminStates.referral_level_value_input.state:
        await state.clear()


def _next_free_level(levels) -> int:
    """Наименьший свободный номер уровня, а не «последний плюс один».

    Уровни — это звенья цепочки, а не очередь. Взяв максимум, редактор после
    удаления второго из трёх предлагал бы только четвёртый, и дыра в середине
    становилась невосстановимой ни из одного интерфейса.

    Сам обход отсутствующий уровень переживает — он его просто пропускает и идёт
    дальше, — но админ остаётся с конфигурацией, которую больше не может починить.
    """
    taken = {lvl.level for lvl in levels}
    candidate = 1
    while candidate in taken:
        candidate += 1
    return candidate


@admin_required
@error_handler
async def show_reward_levels(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext | None = None
):
    await _cancel_pending_input(state)
    await _render_levels(callback, db)
    await callback.answer()


@admin_required
@error_handler
async def toggle_reward_scheme(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext | None = None
):
    """Переключить схему наград.

    Смена схемы меняет то, что бот платит живым людям, поэтому она сознательно
    сделана отдельным действием, а не побочным эффектом создания уровня.
    """
    await _cancel_pending_input(state)
    if bot_configuration_service.is_env_locked('REFERRAL_REWARD_SCHEME'):
        await callback.answer(
            'REFERRAL_REWARD_SCHEME задан в .env и не меняется из админки. '
            'Уберите строку из .env и перезапустите бота.',
            show_alert=True,
        )
        return

    new_value = 'legacy' if settings.is_referral_levels_scheme() else 'levels'
    await bot_configuration_service.set_value(db, 'REFERRAL_REWARD_SCHEME', new_value)

    if new_value == 'levels':
        active = await get_all_reward_levels(db, only_active=True)
        max_depth = settings.get_referral_max_level_depth()
        reachable = [lvl for lvl in active if lvl.level <= max_depth]
        if not active:
            await callback.answer(
                'Схема включена, но активных уровней нет — награды начисляться не будут.',
                show_alert=True,
            )
        elif not reachable:
            # Активные уровни есть, но все глубже предела обхода: цепочка до них
            # не доходит, и «схема включена» без этой оговорки означало бы, что
            # награды пошли, хотя не пойдёт ни одна.
            await callback.answer(
                f'Схема включена, но все активные уровни глубже {max_depth} — '
                'цепочка до них не доходит, награды начисляться не будут.',
                show_alert=True,
            )
        else:
            await callback.answer(f'Схема наград: {new_value}')
    else:
        await callback.answer(f'Схема наград: {new_value}')

    await _render_levels(callback, db)


@admin_required
@error_handler
async def add_reward_level(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext | None = None
):
    await _cancel_pending_input(state)
    levels = await get_all_reward_levels(db)
    next_level = _next_free_level(levels)
    if next_level > MAX_SUPPORTED_LEVEL:
        await callback.answer(f'Максимум {MAX_SUPPORTED_LEVEL} уровней', show_alert=True)
        return

    # Новый уровень заводится ВЫКЛЮЧЕННЫМ и пустым: включение сразу при создании
    # начало бы платить по недозаполненному правилу с ближайшего пополнения.
    await upsert_reward_level(
        db,
        next_level,
        is_active=False,
        reward_mode=ReferralRewardMode.MONEY.value,
        trigger=ReferralRewardTrigger.EVERY_TOPUP.value,
    )
    await callback.answer(f'Уровень {next_level} создан (выключен)')
    await _render_level(callback, db, next_level)


@admin_required
@error_handler
async def import_legacy_settings(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext | None = None
):
    """Перенести действующие настройки ``REFERRAL_*`` в уровень 1.

    Явное действие вместо неявного отката: отката к ``REFERRAL_COMMISSION_PERCENT``
    в расчёте нет, поэтому включение схемы на пустой таблице ничего не платит.
    Кнопка даёт понятный переход — то, что было, становится видимым правилом.

    Повод — «первое пополнение», и это не деталь оформления. В классической схеме
    фиксированные бонусы (пригласившему и приглашённому) разовые: они выдаются один
    раз, за первое пополнение реферала. Повод уровня один на всё правило, поэтому
    перенос с «каждым пополнением» превратил бы оба разовых бонуса в регулярную
    выплату — на живой базе это деньги, которых никто не обещал.

    Плата за такой выбор — процент здесь тоже становится разовым. Недоплатить и
    попросить админа осознанно поменять повод безопаснее, чем переплатить молча;
    правило создаётся выключенным и подписано ровно этим текстом.
    """
    await _cancel_pending_input(state)
    if await get_reward_level(db, 1) is not None:
        await callback.answer('Уровень 1 уже существует', show_alert=True)
        return

    from app.services.referral_reward_service import legacy_percent_for_import

    percent, notes = legacy_percent_for_import()
    await upsert_reward_level(
        db,
        1,
        is_active=False,
        reward_mode=ReferralRewardMode.MONEY.value,
        trigger=ReferralRewardTrigger.FIRST_TOPUP.value,
        referrer_percent=percent,
        referrer_fixed_kopeks=settings.REFERRAL_INVITER_BONUS_KOPEKS or None,
        referee_fixed_kopeks=settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS or None,
        max_payments=settings.REFERRAL_MAX_COMMISSION_PAYMENTS,
    )
    message = (
        'Перенесено в уровень 1 (выключен). Повод — первое пополнение: '
        'фиксированные бонусы в классической схеме разовые. Для комиссии с каждого '
        'пополнения смените повод и уберите фикс. суммы.'
    )
    if notes:
        message += '\n\n' + '\n'.join(f'⚠️ {note}' for note in notes)
    await callback.answer(message, show_alert=True)
    await _render_level(callback, db, 1)


async def _render_level(callback: types.CallbackQuery, db: AsyncSession, level_number: int) -> bool:
    """Отрисовать карточку уровня. ``False`` — уровня нет. Без ``callback.answer()``."""
    level = await get_reward_level(db, level_number)
    if level is None:
        return False

    names = await _tariff_names(db)
    money_on = level.reward_mode in (ReferralRewardMode.MONEY.value, ReferralRewardMode.BOTH.value)
    days_on = level.reward_mode in (ReferralRewardMode.DAYS.value, ReferralRewardMode.BOTH.value)

    beyond_depth = level.level > settings.get_referral_max_level_depth()
    lines = [
        f'🪜 <b>Уровень {level.level}</b>',
        '',
        f'Состояние: {"✅ активен" if level.is_active else "⛔️ выключен"}',
        f'Активные бонусы: {_MODE_LABELS.get(level.reward_mode, level.reward_mode)}',
        f'Повод: {_TRIGGER_LABELS.get(level.trigger, level.trigger)}',
        '',
        '<b>Пригласившему:</b>',
        f'• Процент: {_fmt_optional_percent(level.referrer_percent) if money_on else "выключено режимом"}',
        f'• Фикс. сумма: {_fmt_optional_money(level.referrer_fixed_kopeks) if money_on else "выключено режимом"}',
        f'• Дни: {_fmt_days(level.referrer_days, names.get(level.referrer_tariff_id)) if days_on else "выключено режимом"}',
        '',
        '<b>Приглашённому:</b>',
        f'• Фикс. сумма: {_fmt_optional_money(level.referee_fixed_kopeks) if money_on else "выключено режимом"}',
        f'• Дни: {_fmt_days(level.referee_days, names.get(level.referee_tariff_id)) if days_on else "выключено режимом"}',
        '',
        f'Лимит оплаченных комиссий: {level.max_payments or "без лимита"}',
    ]

    if beyond_depth:
        lines.append('')
        lines.append(
            f'<i>❗️ Цепочка обходится только до {settings.get_referral_max_level_depth()} уровней '
            '(REFERRAL_MAX_LEVEL_DEPTH), поэтому этот уровень не начисляет ничего, '
            'сколько бы ни был настроен.</i>'
        )

    if days_on and not level.referrer_tariff_id and level.referrer_days:
        lines.append('')
        lines.append(
            '<i>Без тарифа дни идут в оплаченную подписку получателя — при нескольких '
            'выбирается с самым поздним сроком; триал берётся, только если платной нет.</i>'
        )

    if not settings.is_referral_levels_scheme():
        lines.append('')
        lines.append(
            '<i>⚠️ Схема наград — классическая: это правило настроено, но НЕ применяется. '
            'Включите многоуровневую схему на экране уровней.</i>'
        )

    if level.level == 1:
        lines.append('')
        lines.append(
            '<i>На первом уровне личный процент партнёра перебивает процент уровня — '
            'в том числе когда процент уровня не задан.</i>'
        )

    # Предупреждение — по каждой стороне отдельно. Общее условие через `and`
    # молчало при половинчатой настройке: тариф выбран пригласившему, а дни
    # приглашённому всё равно теряются у всех, кто без подписки.
    warnings = []
    if days_on and level.referrer_days and not level.referrer_tariff_id:
        warnings.append('пригласившему')
    if days_on and level.referee_days and not level.referee_tariff_id:
        warnings.append('приглашённому')

    if warnings:
        lines.append('')
        lines.append(
            f'<i>⚠️ Тариф не выбран для дней {" и ".join(warnings)}: они лягут в основную '
            'подписку получателя, а если подписки нет — не начислятся вовсе.</i>'
        )

    # В классическом режиме у подписок нет тарифа: правило с тарифом не найдёт
    # подходящую подписку и не начислит НИЧЕГО. Это не поломка движка, а
    # несовместимая настройка, и сказать о ней надо там, где её задают.
    if days_on and not settings.is_multi_tariff_enabled() and (level.referrer_tariff_id or level.referee_tariff_id):
        lines.append('')
        lines.append(
            '<i>❗️ Мультитариф выключен: у подписок нет тарифа, и дни с выбранным '
            'тарифом не начислятся. Уберите тариф — дни пойдут в основную подписку.</i>'
        )

    if (
        days_on
        and level.trigger == ReferralRewardTrigger.REGISTRATION.value
        and level.referee_days
        and not level.referee_tariff_id
    ):
        lines.append(
            '<i>❗️ При поводе «за регистрацию» у приглашённого подписки ещё нет: '
            'без тарифа дни не начислятся никому и никогда.</i>'
        )

    prefix = f'admin_ref_lvl_edit:{level.level}'
    rows = [
        [
            types.InlineKeyboardButton(
                text='⛔️ Выключить' if level.is_active else '✅ Включить',
                callback_data=f'admin_ref_lvl_active:{level.level}',
            )
        ],
        [types.InlineKeyboardButton(text='🎁 Активные бонусы', callback_data=f'admin_ref_lvl_mode:{level.level}')],
        [types.InlineKeyboardButton(text='⚡️ Повод начисления', callback_data=f'admin_ref_lvl_trigger:{level.level}')],
    ]

    if money_on:
        rows.append([types.InlineKeyboardButton(text='％ Процент', callback_data=f'{prefix}:referrer_percent')])
        rows.append(
            [
                types.InlineKeyboardButton(
                    text='💰 Фикс. пригласившему', callback_data=f'{prefix}:referrer_fixed_kopeks'
                ),
                types.InlineKeyboardButton(
                    text='🎁 Фикс. приглашённому', callback_data=f'{prefix}:referee_fixed_kopeks'
                ),
            ]
        )
    if days_on:
        rows.append(
            [
                types.InlineKeyboardButton(text='📅 Дни пригласившему', callback_data=f'{prefix}:referrer_days'),
                types.InlineKeyboardButton(text='📅 Дни приглашённому', callback_data=f'{prefix}:referee_days'),
            ]
        )
        rows.append(
            [
                types.InlineKeyboardButton(
                    text='🎯 Тариф пригласившему', callback_data=f'admin_ref_lvl_tariff:{level.level}:referrer'
                ),
                types.InlineKeyboardButton(
                    text='🎯 Тариф приглашённому', callback_data=f'admin_ref_lvl_tariff:{level.level}:referee'
                ),
            ]
        )

    rows.append([types.InlineKeyboardButton(text='🔢 Лимит комиссий', callback_data=f'{prefix}:max_payments')])
    rows.append(
        [types.InlineKeyboardButton(text='🗑 Удалить уровень', callback_data=f'admin_ref_lvl_delask:{level.level}')]
    )
    rows.append([types.InlineKeyboardButton(text='⬅️ К уровням', callback_data='admin_ref_levels')])

    await callback.message.edit_text('\n'.join(lines), reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))
    return True


@admin_required
@error_handler
async def show_reward_level(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext | None = None
):
    await _cancel_pending_input(state)
    level_number = int(callback.data.split(':')[1])
    if not await _render_level(callback, db, level_number):
        await callback.answer('Уровень не найден', show_alert=True)
        return
    await callback.answer()


@admin_required
@error_handler
async def toggle_level_active(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext | None = None
):
    await _cancel_pending_input(state)
    level_number = int(callback.data.split(':')[1])
    level = await get_reward_level(db, level_number)
    if level is None:
        await callback.answer('Уровень не найден', show_alert=True)
        return

    # Новое состояние вычисляется ДО записи. upsert правит тот же ORM-объект, и
    # чтение level.is_active после него возвращает уже новое значение — тост
    # сообщал ровно противоположное тому, что произошло.
    now_active = not level.is_active
    await upsert_reward_level(db, level_number, is_active=now_active)
    await callback.answer('Уровень включён' if now_active else 'Уровень выключен')
    await _render_level(callback, db, level_number)


@admin_required
@error_handler
async def cycle_level_mode(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext | None = None
):
    """Перебрать активные бонусы уровня: деньги → дни → оба."""
    await _cancel_pending_input(state)
    level_number = int(callback.data.split(':')[1])
    level = await get_reward_level(db, level_number)
    if level is None:
        await callback.answer('Уровень не найден', show_alert=True)
        return

    current_index = _MODE_CYCLE.index(level.reward_mode) if level.reward_mode in _MODE_CYCLE else 0
    new_mode = _MODE_CYCLE[(current_index + 1) % len(_MODE_CYCLE)]
    await upsert_reward_level(db, level_number, reward_mode=new_mode)
    await callback.answer(_MODE_LABELS[new_mode])
    await _render_level(callback, db, level_number)


@admin_required
@error_handler
async def cycle_level_trigger(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext | None = None
):
    await _cancel_pending_input(state)
    level_number = int(callback.data.split(':')[1])
    level = await get_reward_level(db, level_number)
    if level is None:
        await callback.answer('Уровень не найден', show_alert=True)
        return

    current_index = _TRIGGER_CYCLE.index(level.trigger) if level.trigger in _TRIGGER_CYCLE else 0
    new_trigger = _TRIGGER_CYCLE[(current_index + 1) % len(_TRIGGER_CYCLE)]
    await upsert_reward_level(db, level_number, trigger=new_trigger)
    await callback.answer(_TRIGGER_LABELS[new_trigger])
    await _render_level(callback, db, level_number)


@admin_required
@error_handler
async def confirm_delete_level(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext | None = None
):
    """Спросить перед удалением.

    Правило уровня собирают руками, и восстановить его можно только заново набрав
    все поля. Удаление с одного касания, рядом с остальными кнопками карточки,
    слишком легко нажать мимо.
    """
    await _cancel_pending_input(state)
    level_number = int(callback.data.split(':')[1])

    await callback.message.edit_text(
        f'🗑 <b>Удалить уровень {level_number}?</b>\n\n'
        'Настройки правила будут потеряны: восстановить их можно только заново.',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='🗑 Да, удалить', callback_data=f'admin_ref_lvl_del:{level_number}')],
                [types.InlineKeyboardButton(text='⬅️ Отмена', callback_data=f'admin_ref_lvl:{level_number}')],
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_level(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext | None = None):
    await _cancel_pending_input(state)
    level_number = int(callback.data.split(':')[1])
    removed = await delete_reward_level(db, level_number)
    await callback.answer(f'Уровень {level_number} удалён' if removed else 'Уровень уже удалён')
    await _render_levels(callback, db)


@admin_required
@error_handler
async def choose_level_tariff(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext | None = None
):
    await _cancel_pending_input(state)
    _, level_raw, side = callback.data.split(':')
    level_number = int(level_raw)
    level = await get_reward_level(db, level_number)
    current_id = None
    if level is not None:
        current_id = level.referrer_tariff_id if side == 'referrer' else level.referee_tariff_id

    tariffs = list(await get_all_tariffs(db, include_inactive=False))

    # Уже назначенный тариф мог стать неактивным. Список активных его не вернёт, и
    # выбор выглядел бы как «тариф не выбран» — при том, что он выбран и работает.
    # Дописываем его отдельно и помечаем, иначе админ снял бы его не глядя.
    if current_id and all(tariff.id != current_id for tariff in tariffs):
        from app.database.crud.tariff import get_tariff_by_id

        assigned = await get_tariff_by_id(db, current_id)
        if assigned is not None:
            tariffs.insert(0, assigned)

    side_label = 'пригласившему' if side == 'referrer' else 'приглашённому'
    rows = [
        [
            types.InlineKeyboardButton(
                text=('✅ ' if not current_id else '') + '➖ Без тарифа (основная подписка)',
                callback_data=f'admin_ref_lvl_settariff:{level_number}:{side}:0',
            )
        ]
    ]

    shown = tariffs[:_TARIFF_PICKER_LIMIT]
    for tariff in shown:
        mark = '✅ ' if tariff.id == current_id else '🎯 '
        suffix = '' if tariff.is_active else ' (неактивен)'
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=f'{mark}{tariff.name}{suffix}',
                    callback_data=f'admin_ref_lvl_settariff:{level_number}:{side}:{tariff.id}',
                )
            ]
        )
    rows.append([types.InlineKeyboardButton(text='⬅️ Назад', callback_data=f'admin_ref_lvl:{level_number}')])

    text = (
        f'🎯 <b>Тариф для дней {side_label}</b>\n\n'
        'Дни лягут в подписку выбранного тарифа. Если такой подписки у получателя нет, '
        'она будет создана — но только когда у него нет живого триала.\n\n'
        '<i>Без тарифа дни идут в оплаченную подписку получателя; при нескольких '
        'выбирается с самым поздним сроком.</i>'
    )
    # Молчаливое обрезание списка означало бы «такого тарифа нет», хотя он есть.
    if len(tariffs) > len(shown):
        text += f'\n\n<i>⚠️ Показаны первые {len(shown)} из {len(tariffs)} тарифов.</i>'

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@admin_required
@error_handler
async def set_level_tariff(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext | None = None
):
    await _cancel_pending_input(state)
    _, level_raw, side, tariff_raw = callback.data.split(':')
    level_number = int(level_raw)
    tariff_id = int(tariff_raw) or None

    field = 'referrer_tariff_id' if side == 'referrer' else 'referee_tariff_id'
    await upsert_reward_level(db, level_number, **{field: tariff_id})
    await callback.answer('Тариф сохранён')
    await _render_level(callback, db, level_number)


@admin_required
@error_handler
async def start_level_value_edit(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    _, level_raw, field = callback.data.split(':')
    level_number = int(level_raw)
    label, unit, maximum = _NUMERIC_FIELDS[field]

    await state.update_data(referral_level=level_number, referral_field=field)
    await state.set_state(AdminStates.referral_level_value_input)

    hint = f'Введите значение ({unit}).'
    if maximum is not None:
        hint += f' Максимум: {maximum}.'
    if field in _MONEY_FIELDS:
        hint += ' Сумма в рублях, можно дробную.'

    await callback.message.edit_text(
        f'✏️ <b>{label}</b>\nУровень {level_number}\n\n{hint}\n\n0 — не начислять.',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='⬅️ Отмена', callback_data=f'admin_ref_lvl:{level_number}')]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def process_level_value(message: types.Message, db_user: User, db: AsyncSession, state: FSMContext):
    data = await state.get_data()
    level_number = data.get('referral_level')
    field = data.get('referral_field')
    if not level_number or field not in _NUMERIC_FIELDS:
        await state.clear()
        await message.answer('❌ Не понял, какое поле правим. Откройте уровень заново.')
        return

    label, unit, maximum = _NUMERIC_FIELDS[field]
    raw = (message.text or '').strip().replace(',', '.')

    try:
        parsed = float(raw)
    except ValueError:
        await message.answer(f'❌ Нужно число. {label} ({unit}).')
        return

    if parsed < 0:
        await message.answer('❌ Отрицательные значения недопустимы.')
        return

    # Деньги вводятся в рублях, а хранятся в копейках — как и везде в админке.
    value = int(round(parsed * 100)) if field in _MONEY_FIELDS else int(parsed)
    if maximum is not None and value > maximum:
        await message.answer(f'❌ Максимум: {maximum} {unit}.')
        return

    # Ноль в проценте и фиксированной сумме хранится как NULL: в расчёте NULL и 0
    # значат одно и то же — «не начисляется», и держать два представления одного
    # состояния значило бы однажды их спутать.
    if field in _MONEY_FIELDS or field == 'referrer_percent':
        stored = value or None
    else:
        stored = value

    await upsert_reward_level(db, level_number, **{field: stored})
    await state.clear()

    display = settings.format_price(value) if field in _MONEY_FIELDS else f'{value} {unit}'
    await message.answer(
        f'✅ {label}: {display}\n\nОткройте «Уровни наград», чтобы продолжить настройку.',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='🪜 К уровням', callback_data='admin_ref_levels')],
            ]
        ),
    )


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_reward_levels, F.data == 'admin_ref_levels')
    dp.callback_query.register(toggle_reward_scheme, F.data == 'admin_ref_lvl_scheme')
    dp.callback_query.register(add_reward_level, F.data == 'admin_ref_lvl_add')
    dp.callback_query.register(import_legacy_settings, F.data == 'admin_ref_lvl_import')
    # Двоеточие в 'admin_ref_lvl:' обязательно: без него префикс поглотил бы все
    # соседние строки, и любая кнопка уровня открывала бы его карточку. Порядок
    # регистрации при таком разделителе значения не имеет — маршрутизацию
    # целиком проверяет TestCallbackRouting.
    dp.callback_query.register(toggle_level_active, F.data.startswith('admin_ref_lvl_active:'))
    dp.callback_query.register(cycle_level_mode, F.data.startswith('admin_ref_lvl_mode:'))
    dp.callback_query.register(cycle_level_trigger, F.data.startswith('admin_ref_lvl_trigger:'))
    # Более длинный префикс регистрируется раньше: 'admin_ref_lvl_del:' —
    # начало строки 'admin_ref_lvl_delask:', и порядок здесь важен.
    dp.callback_query.register(confirm_delete_level, F.data.startswith('admin_ref_lvl_delask:'))
    dp.callback_query.register(delete_level, F.data.startswith('admin_ref_lvl_del:'))
    dp.callback_query.register(set_level_tariff, F.data.startswith('admin_ref_lvl_settariff:'))
    dp.callback_query.register(choose_level_tariff, F.data.startswith('admin_ref_lvl_tariff:'))
    dp.callback_query.register(start_level_value_edit, F.data.startswith('admin_ref_lvl_edit:'))
    dp.callback_query.register(show_reward_level, F.data.startswith('admin_ref_lvl:'))
    dp.message.register(process_level_value, AdminStates.referral_level_value_input)
