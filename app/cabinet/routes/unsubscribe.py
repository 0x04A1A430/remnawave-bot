"""Публичный эндпоинт отписки от маркетинговых писем.

Намеренно БЕЗ авторизации: по ссылке из письма ходят и почтовые клиенты
(Gmail дёргает POST сам, по RFC 8058), и пользователи, которые давно вышли из
кабинета. Требовать вход — значит гарантированно получить жалобу «Спам»
вместо отписки.

``GET`` отписывает сразу и показывает страницу-подтверждение: почтовые клиенты
всё равно открывают ссылку в браузере, а лишний экран «нажмите, чтобы
подтвердить» роняет конверсию отписки и провоцирует жалобу. ``POST`` — тот же
эффект для one-click.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Depends, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.cabinet.dependencies import get_cabinet_db
from app.cabinet.services.email_unsubscribe import apply_unsubscribe
from app.config import settings


router = APIRouter(prefix='/public', tags=['Cabinet:Public'])


def _page(title: str, message: str, *, ok: bool) -> HTMLResponse:
    """Самодостаточная страница: письма читают где угодно, внешние ресурсы не тянем."""
    service_name = html.escape(settings.SMTP_FROM_NAME or 'VPN')
    cabinet_url = (getattr(settings, 'CABINET_URL', '') or '').strip()
    accent = '#16a34a' if ok else '#dc2626'
    link = (
        f'<p style="margin:22px 0 0"><a href="{html.escape(cabinet_url, quote=True)}" '
        f'style="color:{accent}">Настройки уведомлений в личном кабинете</a></p>'
        if cabinet_url
        else ''
    )
    body = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
</head>
<body style="margin:0;background:#eef0f3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2933">
  <div style="max-width:520px;margin:12vh auto;padding:34px 30px;background:#fff;border:1px solid #e6e8ec;border-top:3px solid {accent};border-radius:16px">
    <h1 style="margin:0 0 12px;font-size:21px;color:#0a0f1a">{html.escape(title)}</h1>
    <p style="margin:0;font-size:15px;line-height:1.6">{html.escape(message)}</p>
    {link}
    <p style="margin:26px 0 0;font-size:12px;color:#98a2b3">&copy; {service_name}</p>
  </div>
</body>
</html>"""
    return HTMLResponse(body, status_code=200 if ok else 400)


async def _unsubscribe(token: str, db: AsyncSession) -> bool:
    if not getattr(settings, 'EMAIL_UNSUBSCRIBE_ENABLED', True):
        return False
    return await apply_unsubscribe(db, token)


@router.get('/unsubscribe', summary='Отписка от маркетинговых писем (ссылка из письма)')
async def unsubscribe_page(token: str = '', db: AsyncSession = Depends(get_cabinet_db)) -> HTMLResponse:
    """Отписывает по токену и показывает результат человеку."""
    if await _unsubscribe(token, db):
        return _page(
            'Вы отписались',
            'Больше не будем присылать новости и промо-предложения на этот адрес. '
            'Письма по вашей подписке — оплата, продление, доступ — продолжат приходить.',
            ok=True,
        )
    return _page(
        'Ссылка не сработала',
        'Ссылка устарела или адрес почты изменился. Отключить рассылки можно в '
        'настройках уведомлений личного кабинета.',
        ok=False,
    )


@router.post('/unsubscribe', summary='One-click отписка (RFC 8058)')
async def unsubscribe_one_click(token: str = '', db: AsyncSession = Depends(get_cabinet_db)) -> Response:
    """Обработчик кнопки «Отписаться» в Gmail/Yahoo.

    Тело запроса (``List-Unsubscribe=One-Click``) не читаем — токен уже в
    query-строке. Отвечаем 200 даже на протухший токен: почтовый клиент
    покажет пользователю ошибку, хотя делать ему с ней нечего.
    """
    await _unsubscribe(token, db)
    return Response(status_code=200)
