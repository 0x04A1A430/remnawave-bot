"""WebSocket endpoint for cabinet real-time notifications."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.database.database import AsyncSessionLocal
from app.database.crud.user import get_user_by_id
from app.config import settings
from app.cabinet.auth.jwt_handler import get_token_payload

logger = logging.getLogger(__name__)

router = APIRouter()


class CabinetConnectionManager:
    """Менеджер WebSocket подключений для кабинета."""

    def __init__(self):
        # user_id -> set of websocket connections
        self._user_connections: Dict[int, Set[WebSocket]] = {}
        # admin user_ids -> set of websocket connections
        self._admin_connections: Dict[int, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, user_id: int, is_admin: bool) -> None:
        """Зарегистрировать подключение."""
        async with self._lock:
            if user_id not in self._user_connections:
                self._user_connections[user_id] = set()
            self._user_connections[user_id].add(websocket)

            if is_admin:
                if user_id not in self._admin_connections:
                    self._admin_connections[user_id] = set()
                self._admin_connections[user_id].add(websocket)

        logger.info(
            "Cabinet WS connected: user_id=%d, is_admin=%s, total_users=%d",
            user_id, is_admin, len(self._user_connections)
        )

    async def disconnect(self, websocket: WebSocket, user_id: int) -> None:
        """Отменить регистрацию подключения."""
        async with self._lock:
            if user_id in self._user_connections:
                self._user_connections[user_id].discard(websocket)
                if not self._user_connections[user_id]:
                    del self._user_connections[user_id]

            if user_id in self._admin_connections:
                self._admin_connections[user_id].discard(websocket)
                if not self._admin_connections[user_id]:
                    del self._admin_connections[user_id]

        logger.info("Cabinet WS disconnected: user_id=%d", user_id)

    async def send_to_user(self, user_id: int, message: dict) -> None:
        """Отправить сообщение конкретному пользователю."""
        # Snapshot connections under the lock to avoid mutation during iteration
        async with self._lock:
            connections = list(self._user_connections.get(user_id, set()))

        if not connections:
            return

        disconnected = set()
        data = json.dumps(message, default=str, ensure_ascii=False)

        for ws in connections:
            try:
                await ws.send_text(data)
            except Exception as e:
                logger.warning("Failed to send to user %d: %s", user_id, e)
                disconnected.add(ws)

        # Cleanup disconnected
        if disconnected:
            async with self._lock:
                for ws in disconnected:
                    self._user_connections.get(user_id, set()).discard(ws)

    async def send_to_admins(self, message: dict) -> None:
        """Отправить сообщение всем админам."""
        # Snapshot connections under the lock to avoid mutation during iteration
        async with self._lock:
            if not self._admin_connections:
                return
            # Create a snapshot: list of (user_id, list of websockets)
            admin_snapshot = [
                (user_id, list(connections))
                for user_id, connections in self._admin_connections.items()
            ]

        data = json.dumps(message, default=str, ensure_ascii=False)
        disconnected_by_user: Dict[int, Set[WebSocket]] = {}

        for user_id, connections in admin_snapshot:
            for ws in connections:
                try:
                    await ws.send_text(data)
                except Exception as e:
                    logger.warning("Failed to send to admin %d: %s", user_id, e)
                    if user_id not in disconnected_by_user:
                        disconnected_by_user[user_id] = set()
                    disconnected_by_user[user_id].add(ws)

        # Cleanup disconnected
        if disconnected_by_user:
            async with self._lock:
                for user_id, ws_set in disconnected_by_user.items():
                    for ws in ws_set:
                        self._admin_connections.get(user_id, set()).discard(ws)


# Глобальный менеджер подключений
cabinet_ws_manager = CabinetConnectionManager()


async def verify_cabinet_ws_token(token: str) -> tuple[int | None, bool]:
    """
    Проверить JWT токен для WebSocket.

    Returns:
        tuple[user_id, is_admin] или (None, False) если токен невалидный
    """
    if not token:
        return None, False

    payload = get_token_payload(token, expected_type="access")
    if not payload:
        return None, False

    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        return None, False

    async with AsyncSessionLocal() as db:
        user = await get_user_by_id(db, user_id)
        if not user or user.status != "active":
            return None, False

        is_admin = settings.is_admin(user.telegram_id)
        return user_id, is_admin


@router.websocket("/ws")
async def cabinet_websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint для real-time уведомлений кабинета."""
    client_host = websocket.client.host if websocket.client else "unknown"

    # Получаем токен из query params
    token = websocket.query_params.get("token")

    if not token:
        logger.warning("Cabinet WS: No token from %s", client_host)
        await websocket.close(code=1008, reason="Unauthorized: No token")
        return

    # Верифицируем токен
    user_id, is_admin = await verify_cabinet_ws_token(token)

    if not user_id:
        logger.warning("Cabinet WS: Invalid token from %s", client_host)
        await websocket.close(code=1008, reason="Unauthorized: Invalid token")
        return

    # Принимаем соединение
    try:
        await websocket.accept()
        logger.info("Cabinet WS accepted: user_id=%d, is_admin=%s", user_id, is_admin)
    except Exception as e:
        logger.error("Cabinet WS: Failed to accept from %s: %s", client_host, e)
        return

    # Регистрируем подключение
    await cabinet_ws_manager.connect(websocket, user_id, is_admin)

    try:
        # Приветственное сообщение
        await websocket.send_json({
            "type": "connected",
            "user_id": user_id,
            "is_admin": is_admin,
        })

        # Обрабатываем входящие сообщения
        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)

                # Ping/pong для keepalive
                if message.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})

            except json.JSONDecodeError:
                logger.warning("Cabinet WS: Invalid JSON from user %d", user_id)
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.exception("Cabinet WS error for user %d: %s", user_id, e)
                break

    except WebSocketDisconnect:
        logger.info("Cabinet WS disconnected: user_id=%d", user_id)
    except Exception as e:
        logger.exception("Cabinet WS error: %s", e)
    finally:
        await cabinet_ws_manager.disconnect(websocket, user_id)


# Функции для отправки уведомлений (используются из других модулей)
async def notify_user_ticket_reply(user_id: int, ticket_id: int, message: str) -> None:
    """Уведомить пользователя об ответе в тикете."""
    await cabinet_ws_manager.send_to_user(user_id, {
        "type": "ticket.admin_reply",
        "ticket_id": ticket_id,
        "message": message,
    })


async def notify_admins_new_ticket(ticket_id: int, title: str, user_id: int) -> None:
    """Уведомить админов о новом тикете."""
    await cabinet_ws_manager.send_to_admins({
        "type": "ticket.new",
        "ticket_id": ticket_id,
        "title": title,
        "user_id": user_id,
    })


async def notify_admins_ticket_reply(ticket_id: int, message: str, user_id: int) -> None:
    """Уведомить админов об ответе пользователя."""
    await cabinet_ws_manager.send_to_admins({
        "type": "ticket.user_reply",
        "ticket_id": ticket_id,
        "message": message,
        "user_id": user_id,
    })
