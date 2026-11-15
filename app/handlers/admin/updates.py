import html
import re

import structlog
from aiogram import Dispatcher, F, types
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.localization.texts import get_texts
from app.services.version_service import version_service
from app.utils.admin_ui import admin_back_button, admin_button, admin_quote, admin_screen
from app.utils.decorators import admin_required, error_handler
from app.utils.miniapp_buttons import strip_leading_emoji


logger = structlog.get_logger(__name__)


def _back_label(value: str) -> str:
    """Подпись кнопки возврата: стрелка в тексте вместо premium-иконки."""

    label = (value or '').strip()
    if not label or label.lower().startswith(('←', '◀', '⬅', '⏪')):
        return label
    return f'← {label}'


# Следы прежнего оформления в локалях: декоративные глифы в начале строк.
_LEGACY_PREFIX_RE = re.compile(
    r'^[ \t]*(?:(?:[▤▣▥◷↻◀▫⚠ℹ→]|NEW(?=[ \t])|i(?=[ \t]))\ufe0f?[ \t]*)*',
    re.MULTILINE,
)


def _clean_legacy(value: str) -> str:
    """Убирает старые декоративные глифы и ведущие пробелы строк из локалей."""
    return _LEGACY_PREFIX_RE.sub('', value).strip()


def get_updates_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)
    buttons = [
        [
            admin_button(
                'Проверить обновления',
                callback_data='admin_updates_check',
                icon='refresh',
            )
        ],
        [
            admin_button(
                'Информация о версии',
                callback_data='admin_updates_info',
                icon='info',
            )
        ],
        [
            admin_button(
                'Открыть репозиторий',
                url=f'https://github.com/{version_service.repo}/releases',
                icon='link',
            )
        ],
        [admin_back_button(texts, 'admin_panel')],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_version_info_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)
    buttons = [
        [
            admin_button(
                strip_leading_emoji(texts.t('ADMIN_UPDATES_025')).strip(),
                callback_data='admin_updates_info',
                icon='refresh',
            )
        ],
        [
            admin_back_button(
                texts,
                'admin_updates',
                label=_back_label(strip_leading_emoji(texts.t('ADMIN_UPDATES_026')).strip()),
            )
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@admin_required
@error_handler
async def show_updates_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(getattr(db_user, 'language', 'ru'))
    try:
        version_info = await version_service.get_version_info()

        current_version = version_info['current_version']
        has_updates = version_info['has_updates']
        total_newer = version_info['total_newer']
        last_check = version_info['last_check']

        status_text = (
            texts.t('ADMIN_UPDATES_006').format(value1=total_newer) if has_updates else texts.t('ADMIN_UPDATES_007')
        )

        last_check_text = ''
        if last_check:
            last_check_text = f'\n<b>Последняя проверка:</b> {last_check.strftime("%d.%m.%Y %H:%M")}'

        version_block = '\n'.join(
            [
                f'<b>Текущая версия:</b> <code>{html.escape(str(current_version))}</code>',
                f'<b>Статус:</b> {status_text}',
                f'<b>Репозиторий:</b> {html.escape(str(version_service.repo))}',
            ]
        )

        message = '\n'.join(
            [
                '<b>Система обновлений</b>',
                '',
                admin_quote(f'{version_block}{last_check_text}'),
                '',
                '<i>Система автоматически проверяет обновления каждый час и отправляет уведомления о новых версиях.</i>',
            ]
        )

        await callback.message.edit_text(
            admin_screen(message, icon='updates', compact=True),
            reply_markup=get_updates_keyboard(db_user.language),
            parse_mode='HTML',
        )
        await callback.answer()

    except Exception as e:
        if 'message is not modified' in str(e).lower():
            logger.debug('Сообщение не изменено в show_updates_menu')
            await callback.answer()
            return
        logger.error('Ошибка показа меню обновлений', error=e)
        await callback.answer(texts.t('ADMIN_UPDATES_027'), show_alert=True)


@admin_required
@error_handler
async def check_updates(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(getattr(db_user, 'language', 'ru'))
    await callback.answer(texts.t('ADMIN_UPDATES_009'))

    try:
        has_updates, newer_releases = await version_service.check_for_updates(force=True)

        if not has_updates:
            message = '\n'.join(
                [
                    '<b>Обновления не найдены</b>',
                    '',
                    admin_quote(
                        '\n'.join(
                            [
                                f'<b>Текущая версия:</b> <code>{html.escape(str(version_service.current_version))}</code>',
                                '<b>Статус:</b> У вас установлена последняя версия',
                                f'<b>Репозиторий:</b> {html.escape(str(version_service.repo))}',
                            ]
                        )
                    ),
                ]
            )

        else:
            updates_list = []
            for i, release in enumerate(newer_releases[:5]):
                icon = version_service.format_version_display(release).split()[0]
                updates_list.append(
                    f'{i + 1}. {icon} <code>{html.escape(str(release.tag_name))}</code> • {release.formatted_date}'
                )

            updates_text = '\n'.join(updates_list)
            more_text = f'\n\nИ еще {len(newer_releases) - 5} обновлений...' if len(newer_releases) > 5 else ''

            message = '\n'.join(
                [
                    '<b>Найдены обновления</b>',
                    '',
                    admin_quote(
                        '\n'.join(
                            [
                                f'<b>Текущая версия:</b> <code>{html.escape(str(version_service.current_version))}</code>',
                                f'<b>Доступно обновлений:</b> {len(newer_releases)}',
                                f'<b>Репозиторий:</b> {html.escape(str(version_service.repo))}',
                            ]
                        )
                    ),
                    '',
                    '<b>Последние версии:</b>',
                    f'{updates_text}{more_text}',
                ]
            )

        keyboard = get_updates_keyboard(db_user.language)

        if has_updates:
            keyboard.inline_keyboard.insert(
                -2,
                [
                    admin_button(
                        strip_leading_emoji(texts.t('ADMIN_UPDATES_032')).strip(),
                        callback_data='admin_updates_info',
                        icon='info',
                    )
                ],
            )

        await callback.message.edit_text(
            admin_screen(message, icon='updates' if has_updates else 'success', compact=True),
            reply_markup=keyboard,
            parse_mode='HTML',
        )

    except Exception as e:
        if 'message is not modified' in str(e).lower():
            logger.debug('Сообщение не изменено в check_updates')
            return
        logger.error('Ошибка проверки обновлений', error=e)
        await callback.message.edit_text(
            admin_screen(
                _clean_legacy(texts.t('ADMIN_UPDATES_028').format(value1=version_service.current_version)),
                icon='error',
                compact=True,
            ),
            reply_markup=get_updates_keyboard(db_user.language),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def show_version_info(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(getattr(db_user, 'language', 'ru'))
    await callback.answer(texts.t('ADMIN_UPDATES_012'))

    try:
        version_info = await version_service.get_version_info()

        current_version = version_info['current_version']
        current_release = version_info['current_release']
        newer_releases = version_info['newer_releases']
        has_updates = version_info['has_updates']
        last_check = version_info['last_check']

        current_info = texts.t('ADMIN_UPDATES_001')

        if current_release:
            current_info += texts.t('ADMIN_UPDATES_013').format(value1=html.escape(str(current_release.tag_name)))
            current_info += texts.t('ADMIN_UPDATES_014').format(value1=current_release.formatted_date)
            if _release_description(current_release, texts):
                current_info += texts.t('ADMIN_UPDATES_019').format(value1=_release_description(current_release, texts))
        else:
            current_info += texts.t('ADMIN_UPDATES_013').format(value1=html.escape(str(current_version)))
            current_info += texts.t('ADMIN_UPDATES_015')

        message_parts = [current_info]

        if has_updates and newer_releases:
            updates_info = texts.t('ADMIN_UPDATES_016')

            for release in newer_releases:
                updates_info += f'<b>{html.escape(str(release.tag_name))}</b>\n'
                updates_info += f'{release.formatted_date}\n'
                if _release_description(release, texts):
                    updates_info += f'{_release_description(release, texts)}\n'
                updates_info += '\n'

            message_parts.append(updates_info.rstrip())

        system_info = texts.t('ADMIN_UPDATES_002')
        system_info += texts.t('ADMIN_UPDATES_003').format(value1=html.escape(str(version_service.repo)))
        system_info += texts.t('ADMIN_UPDATES_004').format(
            value1=texts.t('ADMIN_UPDATES_029') if version_service.enabled else texts.t('ADMIN_UPDATES_030')
        )
        system_info += texts.t('ADMIN_UPDATES_005')

        if last_check:
            system_info += f'<b>Последняя проверка:</b> {last_check.strftime("%d.%m.%Y %H:%M")}\n'

        message_parts.append(system_info.rstrip())

        final_message = _clean_legacy('\n'.join(message_parts))

        if len(final_message) > 4000:
            final_message = final_message[:3900] + texts.t('ADMIN_UPDATES_020')

        await callback.message.edit_text(
            admin_screen(final_message, icon='updates', compact=True),
            reply_markup=get_version_info_keyboard(db_user.language),
            parse_mode='HTML',
            disable_web_page_preview=True,
        )

    except Exception as e:
        if 'message is not modified' in str(e).lower():
            logger.debug('Сообщение не изменено в show_version_info')
            return
        logger.error('Ошибка получения информации о версиях', error=e)
        await callback.message.edit_text(
            admin_screen(
                _clean_legacy(texts.t('ADMIN_UPDATES_031').format(value1=version_service.current_version)),
                icon='error',
                compact=True,
            ),
            reply_markup=get_version_info_keyboard(db_user.language),
            parse_mode='HTML',
        )


def _release_description(release, texts) -> str:
    # Release notes are external content, not Telegram HTML or a translation key.
    if hasattr(release, 'body') and not release.body:
        return texts.t('ADMIN_UPDATES_NO_DESCRIPTION')
    return html.escape(release.short_description)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_updates_menu, F.data == 'admin_updates')

    dp.callback_query.register(check_updates, F.data == 'admin_updates_check')

    dp.callback_query.register(show_version_info, F.data == 'admin_updates_info')
