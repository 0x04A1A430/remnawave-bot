"""Кнопки, которым разрешено работать в групповом админ-чате.

Бот молчит в чужих группах (``ChatTypeFilterMiddleware``), но свою карточку
тикета в групповой админ-чат он кладёт с кнопками действий. В группе работают
только «надёжные» кнопки — обычный callback без ввода текста: FSM-ввод там
невозможен из-за privacy mode бота, а меню админки в общем чате не место.

Единственный источник для фильтра: сторож в тестах сверяет с ним каждую кнопку
групповой карточки (``get_ticket_notification_keyboard(fsm_enabled=False)``),
чтобы новая кнопка не оказалась нарисованной, но мёртвой.
"""

from __future__ import annotations


GROUP_SAFE_CALLBACK_PREFIXES: tuple[str, ...] = (
    'admin_close_ticket_',
    'admin_block_user_perm_ticket_',
    'admin_unblock_user_ticket_',
)

GROUP_SAFE_CALLBACKS: frozenset[str] = frozenset({'admin_support_delete_msg'})


def is_group_safe_callback(data: str | None) -> bool:
    """Можно ли обрабатывать это нажатие вне лички."""
    if not data:
        return False
    return data in GROUP_SAFE_CALLBACKS or data.startswith(GROUP_SAFE_CALLBACK_PREFIXES)
