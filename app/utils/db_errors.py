"""Классификация ошибок БД/сети, которые не требуют админского алерта.

При перезагрузке хоста контейнер бота может проснуться раньше PostgreSQL:
Docker DNS ещё не отдаёт имя `postgres`, asyncpg получает `gaierror`, а
фоновые циклы (retry queue, grace runtime) успевают записать полный traceback
в error-лог и отправить отчёт. Это временное состояние — следующий тик
повторяет работу. Такие ошибки логируем warning'ом без traceback.
"""

from __future__ import annotations

import socket

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError


def is_transient_db_error(error: BaseException) -> bool:
    """True для недоступной БД/сети, которую имеет смысл просто повторить."""
    if isinstance(error, (OSError, TimeoutError, InterfaceError, OperationalError)):
        return True
    if isinstance(error, DBAPIError) and getattr(error, 'connection_invalidated', False):
        return True
    if isinstance(error, socket.gaierror):
        return True

    cause = error.__cause__ or error.__context__
    if cause is not None and cause is not error:
        return is_transient_db_error(cause)
    return False
