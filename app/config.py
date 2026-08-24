import html
import os
import re
from collections import defaultdict
from datetime import time
from pathlib import Path
from typing import ClassVar, Literal
from urllib.parse import quote as _url_quote, urlparse
from zoneinfo import ZoneInfo

import structlog
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


DEFAULT_DISPLAY_NAME_BANNED_KEYWORDS: list[str] = [
    # РџСѓСЃС‚РѕР№ РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ - Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂ РјРѕР¶РµС‚ РґРѕР±Р°РІРёС‚СЊ РєР»СЋС‡РµРІС‹Рµ СЃР»РѕРІР° С‡РµСЂРµР· DISPLAY_NAME_BANNED_KEYWORDS
    # РџСЂРёРјРµСЂС‹: "tme", "joingroup", "support", "admin"
]

USER_TAG_PATTERN = re.compile(r'^[A-Z0-9_]{1,16}$')


logger = structlog.get_logger(__name__)


class Settings(BaseSettings):
    BOT_TOKEN: str
    BOT_USERNAME: str | None = None
    ADMIN_IDS: str = ''
    ADMIN_EMAILS: str = ''  # Comma-separated admin emails for email-only users

    # Test email account for development/testing (bypasses email verification and SMTP)
    TEST_EMAIL: str = ''  # e.g., test@example.com
    TEST_EMAIL_PASSWORD: str = ''  # Password for test account

    SUPPORT_USERNAME: str = '@support'
    # РџСѓР±Р»РёС‡РЅС‹Рµ РєРѕРЅС‚Р°РєС‚С‹ СЃРµСЂРІРёСЃР°, РєРѕС‚РѕСЂС‹Рµ РєР°Р±РёРЅРµС‚ РѕС‚РґР°С‘С‚ РІ GET /info/service.
    # Р”Рѕ СЌС‚РѕРіРѕ С…РµРЅРґР»РµСЂ С‡РёС‚Р°Р» SUPPORT_EMAIL Рё WEBSITE_URL С‡РµСЂРµР· getattr, РЅРѕ С‚Р°РєРёС…
    # РїРѕР»РµР№ РІ Settings РЅРёРєРѕРіРґР° РЅРµ Р±С‹Р»Рѕ вЂ” СЌРЅРґРїРѕРёРЅС‚ РІСЃРµРіРґР° РІРѕР·РІСЂР°С‰Р°Р» None.
    SUPPORT_EMAIL: str | None = None
    SERVICE_WEBSITE_URL: str | None = None
    SUPPORT_MENU_ENABLED: bool = True
    SUPPORT_SYSTEM_MODE: str = 'both'  # one of: tickets, contact, both
    # SLA for support tickets
    SUPPORT_TICKET_SLA_ENABLED: bool = True
    SUPPORT_TICKET_SLA_MINUTES: int = 5
    SUPPORT_TICKET_SLA_CHECK_INTERVAL_SECONDS: int = 60
    SUPPORT_TICKET_SLA_REMINDER_COOLDOWN_MINUTES: int = 15

    # MiniApp tickets settings
    MINIAPP_TICKETS_ENABLED: bool = True  # Enable/disable tickets section in miniapp
    MINIAPP_SUPPORT_TYPE: str = 'tickets'  # one of: tickets, profile, url
    MINIAPP_SUPPORT_URL: str = ''  # Custom URL to redirect when tickets disabled (only for url type)

    ADMIN_NOTIFICATIONS_ENABLED: bool = False
    ADMIN_NOTIFICATIONS_CHAT_ID: str | None = None
    ADMIN_NOTIFICATIONS_RICH_ENABLED: bool = True
    ADMIN_NOTIFICATIONS_TOPIC_ID: int | None = None
    ADMIN_NOTIFICATIONS_TICKET_TOPIC_ID: int | None = None
    ADMIN_NOTIFICATIONS_NALOG_TOPIC_ID: int | None = None

    # Р Р°Р·РґРµР»СЊРЅС‹Рµ С‚РѕРїРёРєРё РґР»СЏ СѓРІРµРґРѕРјР»РµРЅРёР№ (РµСЃР»Рё РЅРµ Р·Р°РґР°РЅРѕ вЂ” fallback РЅР° ADMIN_NOTIFICATIONS_TOPIC_ID)
    ADMIN_NOTIFICATIONS_PURCHASES_TOPIC_ID: int | None = None  # РџРѕРєСѓРїРєРё РїРѕРґРїРёСЃРѕРє
    ADMIN_NOTIFICATIONS_RENEWALS_TOPIC_ID: int | None = None  # РџСЂРѕРґР»РµРЅРёСЏ
    ADMIN_NOTIFICATIONS_TRIALS_TOPIC_ID: int | None = None  # РўСЂРёР°Р»С‹
    ADMIN_NOTIFICATIONS_BALANCE_TOPIC_ID: int | None = None  # РџРѕРїРѕР»РЅРµРЅРёРµ Р±Р°Р»Р°РЅСЃР°
    ADMIN_NOTIFICATIONS_ADDONS_TOPIC_ID: int | None = None  # Р”РѕРєСѓРїРєР° С‚СЂР°С„РёРєР°/СѓСЃС‚СЂРѕР№СЃС‚РІ/СЃРµСЂРІРµСЂРѕРІ
    ADMIN_NOTIFICATIONS_INFRASTRUCTURE_TOPIC_ID: int | None = None  # РќРѕРґС‹, С‚РµС…СЂР°Р±РѕС‚С‹, СЃС‚Р°С‚СѓСЃ РїР°РЅРµР»Рё
    ADMIN_NOTIFICATIONS_ERRORS_TOPIC_ID: int | None = None  # РћС€РёР±РєРё Р±РѕС‚Р°
    ADMIN_NOTIFICATIONS_PROMO_TOPIC_ID: int | None = None  # РџСЂРѕРјРѕРєРѕРґС‹, РєР°РјРїР°РЅРёРё, РїСЂРѕРјРѕРіСЂСѓРїРїС‹
    ADMIN_NOTIFICATIONS_PARTNERS_TOPIC_ID: int | None = None  # РџР°СЂС‚РЅС‘СЂРєРё, РІС‹РІРѕРґС‹, Р°РґРјРёРЅ-РґРµР№СЃС‚РІРёСЏ

    # Per-category enable/disable (default True for backwards compatibility)
    ADMIN_NOTIFICATIONS_PURCHASES_ENABLED: bool = True
    ADMIN_NOTIFICATIONS_RENEWALS_ENABLED: bool = True
    ADMIN_NOTIFICATIONS_TRIALS_ENABLED: bool = True
    ADMIN_NOTIFICATIONS_BALANCE_ENABLED: bool = True
    ADMIN_NOTIFICATIONS_ADDONS_ENABLED: bool = True
    ADMIN_NOTIFICATIONS_INFRASTRUCTURE_ENABLED: bool = True
    ADMIN_NOTIFICATIONS_ERRORS_ENABLED: bool = True
    ADMIN_NOTIFICATIONS_PROMO_ENABLED: bool = True
    ADMIN_NOTIFICATIONS_PARTNERS_ENABLED: bool = True
    ADMIN_NOTIFICATIONS_TICKETS_ENABLED: bool = True

    # РќР°СЃС‚СЂРѕР№РєРё РѕС‡РµСЂРµРґРё С‡РµРєРѕРІ NaloGO
    NALOGO_QUEUE_CHECK_INTERVAL: int = 600  # РРЅС‚РµСЂРІР°Р» РїСЂРѕРІРµСЂРєРё РѕС‡РµСЂРµРґРё (СЃРµРєСѓРЅРґС‹, 10 РјРёРЅ)
    NALOGO_QUEUE_RECEIPT_DELAY: int = 3  # Р—Р°РґРµСЂР¶РєР° РјРµР¶РґСѓ РѕС‚РїСЂР°РІРєРѕР№ С‡РµРєРѕРІ (СЃРµРєСѓРЅРґС‹)
    NALOGO_QUEUE_MAX_ATTEMPTS: int = 72  # РњР°РєСЃРёРјСѓРј РїРѕРїС‹С‚РѕРє РѕС‚РїСЂР°РІРєРё С‡РµРєР° (72 Г— 10РјРёРЅ = 12 С‡Р°СЃРѕРІ)

    ADMIN_REPORTS_ENABLED: bool = False
    ADMIN_REPORTS_CHAT_ID: str | None = None
    ADMIN_REPORTS_TOPIC_ID: int | None = None
    ADMIN_REPORTS_SEND_TIME: str | None = None

    CHANNEL_IS_REQUIRED_SUB: bool = False
    CHANNEL_DISABLE_TRIAL_ON_UNSUBSCRIBE: bool = True
    CHANNEL_REQUIRED_FOR_ALL: bool = False

    DATABASE_URL: str | None = None

    POSTGRES_HOST: str = 'postgres'
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = 'remnawave_bot'
    POSTGRES_USER: str = 'remnawave_user'
    POSTGRES_PASSWORD: str = 'secure_password_123'

    SQLITE_PATH: str = './data/bot.db'
    LOCALES_PATH: str = './locales'

    TIMEZONE: str = Field(default_factory=lambda: os.getenv('TZ', 'UTC'))

    # strftime pattern used to render datetime values that flow into
    # email templates (subscription_expiring, subscription_renewed,
    # autopay_success, etc). Default is locale-independent so it
    # renders identically on every system: '20.05.2026, 10:32'.
    # Admins who run a Docker image with the matching locale package
    # installed can switch to `'%d %B %Y, %H:%M'` for month names like
    # '20 РјР°СЏ 2026, 10:32', or to `'%Y-%m-%d %H:%M'` for ISO-ish.
    # See app/utils/timezone.py::format_email_datetime.
    EMAIL_DATE_FORMAT: str = '%d.%m.%Y, %H:%M'

    DATABASE_MODE: str = 'auto'

    # РџР°СЂР°РјРµС‚СЂС‹ РїСѓР»Р° РїРѕРґРєР»СЋС‡РµРЅРёР№ Рє PostgreSQL. Р Р°РЅСЊС€Рµ Р±С‹Р»Рё Р·Р°С…Р°СЂРґРєРѕР¶РµРЅС‹ РІ
    # app/database/database.py вЂ” РІС‹РЅРµСЃРµРЅС‹ РІ .env, С‡С‚РѕР±С‹ РјР°СЃС€С‚Р°Р±РёСЂРѕРІР°С‚СЊ РїСѓР» РїРѕРґ
    # РЅР°РіСЂСѓР·РєСѓ Р±РµР· РїРµСЂРµСЃР±РѕСЂРєРё РѕР±СЂР°Р·Р°. РџСЂРё РЅРµСЃРєРѕР»СЊРєРёС… РІРѕСЂРєРµСЂР°С… РєР°Р¶РґС‹Р№ РїСЂРѕС†РµСЃСЃ
    # РґРµСЂР¶РёС‚ РЎР’РћР™ РїСѓР», РїРѕСЌС‚РѕРјСѓ СЃСѓРјРјР°СЂРЅРѕ в‰€ WORKERS * (POOL_SIZE + MAX_OVERFLOW)
    # СЃРѕРµРґРёРЅРµРЅРёР№ вЂ” РґРµСЂР¶РёС‚Рµ РЅРёР¶Рµ max_connections PostgreSQL. Р”Р»СЏ SQLite РЅРµ
    # РїСЂРёРјРµРЅСЏСЋС‚СЃСЏ (С‚Р°Рј РёСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ NullPool Р±РµР· РїСѓР»РёРЅРіР°).
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_POOL_TIMEOUT: int = 30

    REDIS_URL: str = 'redis://localhost:6379/0'
    CART_TTL_SECONDS: int = 3600  # Р’СЂРµРјСЏ Р¶РёР·РЅРё РєРѕСЂР·РёРЅС‹ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ РІ Redis (1 С‡Р°СЃ)
    # В«РЎРІРµР¶РµРµ РЅР°РјРµСЂРµРЅРёРµВ» РїРѕРїРѕР»РЅРёС‚СЊ СЂР°РґРё СЃРѕС…СЂР°РЅС‘РЅРЅРѕР№ РєРѕСЂР·РёРЅС‹. РўРёС…Р°СЏ Р°РІС‚Рѕ-РїРѕРєСѓРїРєР° РёР·
    # РєРѕСЂР·РёРЅС‹ РїРѕСЃР»Рµ РїРѕРїРѕР»РЅРµРЅРёСЏ СЃСЂР°Р±Р°С‚С‹РІР°РµС‚ РўРћР›Р¬РљРћ РµСЃР»Рё РІ С‚РµС‡РµРЅРёРµ СЌС‚РѕРіРѕ РѕРєРЅР° СЋР·РµСЂ
    # СЏРІРЅРѕ РЅР°Р¶Р°Р» В«РљРѕСЂР·РёРЅР° СЃРѕС…СЂР°РЅРµРЅР° в†’ РІС‹Р±СЂР°С‚СЊ РѕРїР»Р°С‚СѓВ» (return_to_cart). РРЅР°С‡Рµ
    # РїРѕРїРѕР»РЅРµРЅРёРµ СЂР°РґРё РїРѕРґР°СЂРєР° / РїСЂРѕСЃС‚Рѕ РґРµРЅРµРі РЅРµ РґРѕР»Р¶РЅРѕ РјРѕР»С‡Р° С‚СЂР°С‚РёС‚СЊСЃСЏ РЅР° РїРѕРґРїРёСЃРєСѓ.
    CART_AUTOPURCHASE_INTENT_TTL_SECONDS: int = 1800  # 30 РјРёРЅСѓС‚ (С…РІР°С‚Р°РµС‚ РЅР° РѕРїР»Р°С‚Сѓ, РЅРѕ РЅРµ РЅР° В«Р·Р°Р±С‹С‚СѓСЋВ» РєРѕСЂР·РёРЅСѓ)

    REMNAWAVE_API_URL: str | None = None
    REMNAWAVE_API_KEY: str | None = None
    REMNAWAVE_SECRET_KEY: str | None = None

    # HTTP-С‚Р°Р№РјР°СѓС‚С‹ Р·Р°РїСЂРѕСЃРѕРІ Рє РїР°РЅРµР»Рё RemnaWave (СЃРµРєСѓРЅРґС‹). Self-hosted РїР°РЅРµР»Рё
    # Р±С‹РІР°СЋС‚ РјРµРґР»РµРЅРЅС‹РјРё РЅР° РєРѕРЅРЅРµРєС‚: СЂР°РЅСЊС€Рµ connect Р±С‹Р» Р·Р°С€РёС‚ РІ 10СЃ, РёР·-Р·Р° С‡РµРіРѕ
    # РЅР° РјРµРґР»РµРЅРЅРѕР№ РїР°РЅРµР»Рё СЃРѕРµРґРёРЅРµРЅРёРµ СЂРІР°Р»РѕСЃСЊ (ConnectionTimeoutError). РўСЂР°РЅР·РёРµРЅС‚РЅС‹Рµ
    # С‚Р°Р№РјР°СѓС‚С‹ Р»РѕРіРёСЂСѓСЋС‚СЃСЏ РєР°Рє WARNING, С‡С‚РѕР±С‹ РЅРµ СЃРїР°РјРёС‚СЊ Р°РґРјРёРЅ-С‡Р°С‚ РѕС€РёР±РєР°РјРё.
    REMNAWAVE_API_CONNECT_TIMEOUT: int = 30
    REMNAWAVE_API_TOTAL_TIMEOUT: int = 60

    REMNAWAVE_USERNAME: str | None = None
    REMNAWAVE_PASSWORD: str | None = None
    REMNAWAVE_CADDY_TOKEN: str | None = None
    REMNAWAVE_AUTH_TYPE: str = 'api_key'  # api_key, basic, bearer, cookies, caddy
    REMNAWAVE_USER_DESCRIPTION_TEMPLATE: str = 'Bot user: {full_name} {username}'
    REMNAWAVE_USER_USERNAME_TEMPLATE: str = 'user_{telegram_id}'
    REMNAWAVE_USER_DELETE_MODE: str = 'delete'  # "delete" РёР»Рё "disable"
    REMNAWAVE_AUTO_SYNC_ENABLED: bool = False
    REMNAWAVE_AUTO_SYNC_TIMES: str = '03:00'
    REMNAWAVE_USE_USER_ID: bool = False  # v3.0.0: РїРµСЂРµРєР»СЋС‡РёС‚СЊ РЅР° True РґР»СЏ РёСЃРїРѕР»СЊР·РѕРІР°РЅРёСЏ userId РІРјРµСЃС‚Рѕ uuid
    CABINET_REMNA_SUB_CONFIG: str | None = None  # UUID РєРѕРЅС„РёРіР° СЃС‚СЂР°РЅРёС†С‹ РїРѕРґРїРёСЃРєРё РёР· RemnaWave

    # RemnaWave incoming webhooks (real-time event delivery from backend)
    REMNAWAVE_WEBHOOK_ENABLED: bool = False
    REMNAWAVE_WEBHOOK_PATH: str = '/remnawave-webhook'
    REMNAWAVE_WEBHOOK_SECRET: str | None = None  # HMAC-SHA256 shared secret (min 32 chars)
    REMNAWAVE_WEBHOOK_NOTIFY_NODE_CONNECTION_STATUS: bool = True
    # Coalescing knobs РґР»СЏ burst'РѕРІ node.connection_lost / node.connection_restored.
    # РћРєРЅРѕ вЂ” СЃРєРѕР»СЊРєРѕ СЃРµРєСѓРЅРґ Р±СѓС„РµСЂРёРј СЃРѕР±С‹С‚РёСЏ РѕРґРЅРѕРіРѕ С‚РёРїР° РїРµСЂРµРґ РѕС‚РїСЂР°РІРєРѕР№ РѕРґРЅРѕР№
    # СЃРІРѕРґРєРё. Cap вЂ” Р¶С‘СЃС‚РєРёР№ Р»РёРјРёС‚ СЂР°Р·РјРµСЂР° Р±СѓС„РµСЂР° РЅР° event_name (Р·Р°С‰РёС‚Р° РѕС‚
    # mem-DoS РїСЂРё РєРѕРјРїСЂРѕРјРµС‚Р°С†РёРё webhook-СЃРµРєСЂРµС‚Р°). РЎРј. RemnaWaveWebhookService.
    REMNAWAVE_WEBHOOK_NODE_COALESCE_WINDOW_SECONDS: float = 10.0
    REMNAWAVE_WEBHOOK_NODE_BUFFER_MAX: int = 500

    # Webhook user notification toggles (what Telegram messages users receive from webhook events)
    WEBHOOK_NOTIFY_USER_ENABLED: bool = True
    WEBHOOK_NOTIFY_SUB_STATUS: bool = True
    WEBHOOK_NOTIFY_SUB_EXPIRED: bool = True
    WEBHOOK_NOTIFY_SUB_EXPIRING: bool = True
    WEBHOOK_NOTIFY_SUB_LIMITED: bool = True
    WEBHOOK_NOTIFY_TRAFFIC_RESET: bool = True
    WEBHOOK_NOTIFY_SUB_DELETED: bool = True
    WEBHOOK_NOTIFY_SUB_REVOKED: bool = True
    WEBHOOK_NOTIFY_FIRST_CONNECTED: bool = True
    WEBHOOK_NOTIFY_NOT_CONNECTED: bool = True
    WEBHOOK_NOTIFY_BANDWIDTH_THRESHOLD: bool = True
    WEBHOOK_NOTIFY_DEVICES: bool = True
    WEBHOOK_NOTIFY_TORRENT_DETECTED: bool = True

    TRIAL_DURATION_DAYS: int = 3
    TRIAL_TRAFFIC_LIMIT_GB: int = 10
    TRIAL_DEVICE_LIMIT: int = 2
    TRIAL_ADD_REMAINING_DAYS_TO_PAID: bool = False
    TRIAL_PAYMENT_ENABLED: bool = False
    TRIAL_ACTIVATION_PRICE: int = 0
    TRIAL_USER_TAG: str | None = None
    TRIAL_DISABLED_FOR: str = 'none'  # none, email, telegram, all
    DEFAULT_TRAFFIC_LIMIT_GB: int = 100
    DEFAULT_DEVICE_LIMIT: int = 1
    DEFAULT_TRAFFIC_RESET_STRATEGY: str = 'MONTH'
    RESET_TRAFFIC_ON_PAYMENT: bool = False
    RESET_TRAFFIC_ON_TARIFF_SWITCH: bool = True
    RESET_DEVICES_ON_RENEWAL: bool = False
    TARIFF_SWITCH_UPGRADE_ENABLED: bool = True
    TARIFF_SWITCH_DOWNGRADE_ENABLED: bool = True
    # РџСЂРё СЃРјРµРЅРµ С‚Р°СЂРёС„Р° РќР• РїРµСЂРµРЅРѕСЃРёС‚СЊ РѕСЃС‚Р°С‚РѕРє РґРЅРµР№, РЅР°СЃРїР°РјР»РµРЅРЅС‹С… РЅР° Р±РµСЃРїР»Р°С‚РЅРѕРј (0в‚Ѕ)
    # С‚Р°СЂРёС„Рµ, РЅР° РЅРѕРІС‹Р№ РїР»Р°С‚РЅС‹Р№ С‚Р°СЂРёС„ (РёРЅР°С‡Рµ СЋР·РµСЂ Р±РµСЃРїР»Р°С‚РЅРѕ СѓРЅРѕСЃРёС‚, РЅР°РїСЂ., 1000 РґРЅРµР№).
    # РџР»Р°С‚РЅС‹Рµ РїРѕРґРїРёСЃРєРё РїРµСЂРµРЅРѕСЃСЏС‚ РґРЅРё РєР°Рє РѕР±С‹С‡РЅРѕ. Р’С‹РєР»СЋС‡РёС‚Рµ, С‡С‚РѕР±С‹ РІРµСЂРЅСѓС‚СЊ РїРµСЂРµРЅРѕСЃ.
    TARIFF_SWITCH_RESET_FREE_DAYS: bool = True
    MAX_DEVICES_LIMIT: int = 20

    TRIAL_WARNING_HOURS: int = 2
    ENABLE_NOTIFICATIONS: bool = True
    NOTIFICATION_RETRY_ATTEMPTS: int = 3

    MONITORING_LOGS_RETENTION_DAYS: int = 30
    NOTIFICATION_CACHE_HOURS: int = 24

    SERVER_STATUS_MODE: str = 'disabled'
    SERVER_STATUS_EXTERNAL_URL: str | None = None
    SERVER_STATUS_METRICS_URL: str | None = None
    SERVER_STATUS_METRICS_USERNAME: str | None = None
    SERVER_STATUS_METRICS_PASSWORD: str | None = None
    SERVER_STATUS_METRICS_VERIFY_SSL: bool = True
    SERVER_STATUS_REQUEST_TIMEOUT: int = 10
    SERVER_STATUS_ITEMS_PER_PAGE: int = 10

    # === Grace access settings (restricted temporary access after exhaustion) ===
    GRACE_ACCESS_MODE: str = 'false'  # 'false' | 'observe' | 'true' | 'drain'
    GRACE_ACCESS_DURATION_HOURS: int = 72
    GRACE_ACCESS_EXPIRED_SQUAD_UUID: str = ''
    GRACE_ACCESS_LIMITED_SQUAD_UUID: str = ''
    GRACE_ACCESS_EXTERNAL_SQUAD_UUID: str = ''
    GRACE_ACCESS_TRAFFIC_GB: int = 1
    GRACE_ACCESS_TRIAL_ENABLED: bool = False
    GRACE_ACCESS_DAILY_ENABLED: bool = False
    GRACE_ACCESS_FREE_ENABLED: bool = False
    GRACE_ACCESS_RECONCILE_INTERVAL_SECONDS: int = 60
    GRACE_ACCESS_RECONCILE_BATCH_SIZE: int = 200
    GRACE_ACCESS_CANDIDATE_LOOKBACK_MINUTES: int = 30

    BASE_SUBSCRIPTION_PRICE: int = 50000
    AVAILABLE_SUBSCRIPTION_PERIODS: str = '14,30,60,90,180,360'
    AVAILABLE_RENEWAL_PERIODS: str = '30,90,180'
    PRICE_14_DAYS: int = 50000
    PRICE_30_DAYS: int = 99000
    PRICE_60_DAYS: int = 189000
    PRICE_90_DAYS: int = 269000
    PRICE_180_DAYS: int = 499000
    PRICE_360_DAYS: int = 899000
    PAID_SUBSCRIPTION_USER_TAG: str | None = None
    GRACE_USER_TAG: str | None = 'GRACE'

    PRICE_TRAFFIC_5GB: int = 2000
    PRICE_TRAFFIC_10GB: int = 3500
    PRICE_TRAFFIC_25GB: int = 7000
    PRICE_TRAFFIC_50GB: int = 11000
    PRICE_TRAFFIC_100GB: int = 15000
    PRICE_TRAFFIC_250GB: int = 17000
    PRICE_TRAFFIC_500GB: int = 19000
    PRICE_TRAFFIC_1000GB: int = 19500
    PRICE_TRAFFIC_UNLIMITED: int = 20000

    TRAFFIC_PACKAGES_CONFIG: str = ''

    PRICE_PER_DEVICE: int = 5000
    DEVICES_SELECTION_ENABLED: bool = True
    DEVICES_SELECTION_DISABLED_AMOUNT: int | None = None

    BASE_PROMO_GROUP_PERIOD_DISCOUNTS_ENABLED: bool = False
    BASE_PROMO_GROUP_PERIOD_DISCOUNTS: str = ''

    # Р РµР¶РёРј РІС‹Р±РѕСЂР° С‚СЂР°С„РёРєР°:
    # - selectable: РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ РІС‹Р±РёСЂР°РµС‚ С‚СЂР°С„РёРє РїСЂРё РїРѕРєСѓРїРєРµ Рё РјРѕР¶РµС‚ РґРѕРєСѓРїР°С‚СЊ
    # - fixed: С„РёРєСЃРёСЂРѕРІР°РЅРЅС‹Р№ Р»РёРјРёС‚, Р±РµР· РІС‹Р±РѕСЂР° Рё Р±РµР· РґРѕРєСѓРїРєРё
    # - fixed_with_topup: С„РёРєСЃРёСЂРѕРІР°РЅРЅС‹Р№ Р»РёРјРёС‚ РїСЂРё РїРѕРєСѓРїРєРµ, РЅРѕ РґРѕРєСѓРїРєР° СЂР°Р·СЂРµС€РµРЅР° (РїСЂРё РїСЂРѕРґР»РµРЅРёРё СЃР±СЂРѕСЃ РґРѕ Р»РёРјРёС‚Р°)
    TRAFFIC_SELECTION_MODE: str = 'selectable'
    FIXED_TRAFFIC_LIMIT_GB: int = 100
    BUY_TRAFFIC_BUTTON_VISIBLE: bool = True

    # Р РµР¶РёРј РїСЂРѕРґР°Р¶ РїРѕРґРїРёСЃРѕРє:
    # - classic: РєР»Р°СЃСЃРёС‡РµСЃРєРёР№ СЂРµР¶РёРј (РІС‹Р±РѕСЂ СЃРµСЂРІРµСЂРѕРІ, С‚СЂР°С„РёРєР°, СѓСЃС‚СЂРѕР№СЃС‚РІ, РїРµСЂРёРѕРґР° РѕС‚РґРµР»СЊРЅРѕ)
    # - tariffs: СЂРµР¶РёРј С‚Р°СЂРёС„РѕРІ (РіРѕС‚РѕРІС‹Рµ РїР°РєРµС‚С‹ СЃ С„РёРєСЃРёСЂРѕРІР°РЅРЅС‹РјРё РїР°СЂР°РјРµС‚СЂР°РјРё)
    SALES_MODE: str = 'tariffs'

    # Multi-tariff mode: allows users to purchase multiple tariffs simultaneously
    # Only works when SALES_MODE='tariffs'
    MULTI_TARIFF_ENABLED: bool = False
    MAX_ACTIVE_SUBSCRIPTIONS: int = 10

    # ID С‚Р°СЂРёС„Р° РґР»СЏ С‚СЂРёР°Р»Р° РІ СЂРµР¶РёРјРµ С‚Р°СЂРёС„РѕРІ (0 = РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ СЃС‚Р°РЅРґР°СЂС‚РЅС‹Рµ РЅР°СЃС‚СЂРѕР№РєРё С‚СЂРёР°Р»Р°)
    # Р•СЃР»Рё СѓРєР°Р·Р°РЅ ID С‚Р°СЂРёС„Р°, РїР°СЂР°РјРµС‚СЂС‹ С‚СЂРёР°Р»Р° Р±РµСЂСѓС‚СЃСЏ РёР· С‚Р°СЂРёС„Р° (traffic_limit_gb, device_limit, allowed_squads)
    # Р”Р»РёС‚РµР»СЊРЅРѕСЃС‚СЊ С‚СЂРёР°Р»Р° РІСЃС‘ СЂР°РІРЅРѕ Р±РµСЂС‘С‚СЃСЏ РёР· TRIAL_DURATION_DAYS
    TRIAL_TARIFF_ID: int = 0

    # РќР°СЃС‚СЂРѕР№РєРё РґРѕРєСѓРїРєРё С‚СЂР°С„РёРєР°
    TRAFFIC_TOPUP_ENABLED: bool = True  # Р’РєР»СЋС‡РёС‚СЊ/РІС‹РєР»СЋС‡РёС‚СЊ С„СѓРЅРєС†РёСЋ РґРѕРєСѓРїРєРё С‚СЂР°С„РёРєР°
    # РџР°РєРµС‚С‹ РґР»СЏ РґРѕРєСѓРїРєРё С‚СЂР°С„РёРєР° (С„РѕСЂРјР°С‚: "РіР±:С†РµРЅР°:enabled", РїСѓСЃС‚Р°СЏ СЃС‚СЂРѕРєР° = РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ TRAFFIC_PACKAGES_CONFIG)
    TRAFFIC_TOPUP_PACKAGES_CONFIG: str = ''

    # РќР°СЃС‚СЂРѕР№РєРё СЃР±СЂРѕСЃР° С‚СЂР°С„РёРєР°
    # Р РµР¶РёРјС‹ СЂР°СЃС‡РµС‚Р° С†РµРЅС‹ СЃР±СЂРѕСЃР°:
    # "period" - С„РёРєСЃРёСЂРѕРІР°РЅРЅР°СЏ С†РµРЅР° = СЃС‚РѕРёРјРѕСЃС‚СЊ РїРµСЂРёРѕРґР° 30 РґРЅРµР№ (СЃС‚Р°СЂРѕРµ РїРѕРІРµРґРµРЅРёРµ)
    # "traffic" - С†РµРЅР° Р·Р°РІРёСЃРёС‚ РѕС‚ С‚РµРєСѓС‰РµРіРѕ Р»РёРјРёС‚Р° С‚СЂР°С„РёРєР° (С†РµРЅР° РїР°РєРµС‚Р° С‚СЂР°С„РёРєР°)
    # "traffic_with_purchased" - С†РµРЅР° = Р±Р°Р·РѕРІС‹Р№ С‚СЂР°С„РёРє + РґРѕРєСѓРїР»РµРЅРЅС‹Р№ С‚СЂР°С„РёРє (СЂРµРєРѕРјРµРЅРґСѓРµС‚СЃСЏ)
    TRAFFIC_RESET_PRICE_MODE: str = 'traffic_with_purchased'
    # Р‘Р°Р·РѕРІР°СЏ С†РµРЅР° СЃР±СЂРѕСЃР° РІ РєРѕРїРµР№РєР°С… (РёСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ РµСЃР»Рё СЂРµР¶РёРј "period" РёР»Рё РєР°Рє РјРёРЅРёРјР°Р»СЊРЅР°СЏ С†РµРЅР°)
    TRAFFIC_RESET_BASE_PRICE: int = 0  # 0 = РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ PERIOD_PRICES[30]

    REFERRAL_MINIMUM_TOPUP_KOPEKS: int = 10000
    REFERRAL_FIRST_TOPUP_BONUS_KOPEKS: int = 10000
    REFERRAL_INVITER_BONUS_KOPEKS: int = 10000
    REFERRAL_COMMISSION_PERCENT: int = 25
    REFERRAL_FIRST_PAYMENT_COMMISSION_PERCENT: int | None = None
    REFERRAL_RECURRING_COMMISSION_TIERS: str = ''  # Р¤РѕСЂРјР°С‚: "0:10,10:15,50:20,100:25"
    REFERRAL_MAX_COMMISSION_PAYMENTS: int = 0  # РњР°РєСЃ. РєРѕР»-РІРѕ РїР»Р°С‚РµР¶РµР№ СЂРµС„РµСЂР°Р»Р° СЃ РєРѕРјРёСЃСЃРёРµР№ (0 = Р±РµР· Р»РёРјРёС‚Р°)

    REFERRAL_PROGRAM_ENABLED: bool = True
    REFERRAL_NOTIFICATIONS_ENABLED: bool = True
    REFERRAL_NOTIFICATION_RETRY_ATTEMPTS: int = 3

    # РќР°СЃС‚СЂРѕР№РєРё РІС‹РІРѕРґР° СЂРµС„РµСЂР°Р»СЊРЅРѕРіРѕ Р±Р°Р»Р°РЅСЃР°
    REFERRAL_WITHDRAWAL_ENABLED: bool = False  # Р’РєР»СЋС‡РёС‚СЊ РІРѕР·РјРѕР¶РЅРѕСЃС‚СЊ РІС‹РІРѕРґР°
    REFERRAL_WITHDRAWAL_MIN_AMOUNT_KOPEKS: int = 100000  # РњРёРЅ. СЃСѓРјРјР° РІС‹РІРѕРґР° (1000в‚Ѕ)
    REFERRAL_WITHDRAWAL_COOLDOWN_DAYS: int = 30  # Р§Р°СЃС‚РѕС‚Р° Р·Р°РїСЂРѕСЃРѕРІ РЅР° РІС‹РІРѕРґ
    REFERRAL_WITHDRAWAL_ONLY_REFERRAL_BALANCE: bool = True  # РўРѕР»СЊРєРѕ СЂРµС„. Р±Р°Р»Р°РЅСЃ (False = СЂРµС„ + СЃРІРѕР№)
    REFERRAL_WITHDRAWAL_REQUISITES_TEXT: str = ''  # РўРµРєСЃС‚-РїРѕРґСЃРєР°Р·РєР° РґР»СЏ СЂРµРєРІРёР·РёС‚РѕРІ РїСЂРё РІС‹РІРѕРґРµ
    REFERRAL_WITHDRAWAL_NOTIFICATIONS_TOPIC_ID: int | None = None  # РўРѕРїРёРє РґР»СЏ СѓРІРµРґРѕРјР»РµРЅРёР№
    REFERRAL_PARTNER_SECTION_VISIBLE: bool = True  # РџРѕРєР°Р·С‹РІР°С‚СЊ СЂР°Р·РґРµР» РїР°СЂС‚РЅС‘СЂРєРё РІ РєР°Р±РёРЅРµС‚Рµ

    # РќР°СЃС‚СЂРѕР№РєРё Р°РЅР°Р»РёР·Р° РЅР° РїРѕРґРѕР·СЂРёС‚РµР»СЊРЅРѕСЃС‚СЊ
    REFERRAL_WITHDRAWAL_SUSPICIOUS_MIN_DEPOSIT_KOPEKS: int = 50000  # РњРёРЅ. СЃСѓРјРјР° РѕС‚ 1 СЂРµС„РµСЂР°Р»Р° (500в‚Ѕ)
    REFERRAL_WITHDRAWAL_SUSPICIOUS_MAX_DEPOSITS_PER_MONTH: int = 10  # РњР°РєСЃ. РїРѕРїРѕР»РЅРµРЅРёР№ РѕС‚ 1 СЂРµС„РµСЂР°Р»Р°/РјРµСЃ
    REFERRAL_WITHDRAWAL_SUSPICIOUS_NO_PURCHASES_RATIO: float = 2.0  # РџРѕРїРѕР»РЅРёР» РІ X СЂР°Р· Р±РѕР»СЊС€Рµ С‡РµРј РїРѕС‚СЂР°С‚РёР»

    # РўРµСЃС‚РѕРІС‹Р№ СЂРµР¶РёРј РґР»СЏ РІС‹РІРѕРґР° (РїРѕР·РІРѕР»СЏРµС‚ Р°РґРјРёРЅР°Рј РІСЂСѓС‡РЅСѓСЋ РЅР°С‡РёСЃР»СЏС‚СЊ СЂРµС„. РґРѕС…РѕРґ)
    REFERRAL_WITHDRAWAL_TEST_MODE: bool = False

    # РљРѕРЅРєСѓСЂСЃС‹ (РіР»РѕР±Р°Р»СЊРЅС‹Р№ С„Р»Р°Рі, Р±СѓРґРµС‚ СЂР°СЃС€РёСЂСЏС‚СЊСЃСЏ РїРѕРґ СЂР°Р·РЅС‹Рµ С‚РёРїС‹)
    CONTESTS_ENABLED: bool = False
    CONTESTS_BUTTON_VISIBLE: bool = False
    # Р”Р»СЏ РѕР±СЂР°С‚РЅРѕР№ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚Рё СЃРѕ СЃС‚Р°СЂС‹РјРё РєРѕРЅС„РёРіР°РјРё
    REFERRAL_CONTESTS_ENABLED: bool = False

    BLACKLIST_CHECK_ENABLED: bool = False
    BLACKLIST_GITHUB_URL: str | None = None
    BLACKLIST_UPDATE_INTERVAL_HOURS: int = 24
    BLACKLIST_IGNORE_ADMINS: bool = True

    DISPOSABLE_EMAIL_CHECK_ENABLED: bool = True

    # РќР°СЃС‚СЂРѕР№РєРё РїРµСЂРµРІС‹РїСѓСЃРєР° РїРѕРґРїРёСЃРєРё (revoke + regenerate link)
    SUBSCRIPTION_REVOKE_ENABLED: bool = True
    SUBSCRIPTION_REVOKE_COOLDOWN_SECONDS: int = 43200  # 12 hours

    # РќР°СЃС‚СЂРѕР№РєРё РїСЂРѕСЃС‚РѕР№ РїРѕРєСѓРїРєРё
    SIMPLE_SUBSCRIPTION_ENABLED: bool = False
    SIMPLE_SUBSCRIPTION_PERIOD_DAYS: int = 30
    SIMPLE_SUBSCRIPTION_DEVICE_LIMIT: int = 1
    SIMPLE_SUBSCRIPTION_TRAFFIC_GB: int = 0  # 0 РѕР·РЅР°С‡Р°РµС‚ Р±РµР·Р»РёРјРёС‚
    SIMPLE_SUBSCRIPTION_SQUAD_UUID: str | None = None

    # РќР°СЃС‚СЂРѕР№РєРё РєРѕРЅСЃС‚СЂСѓРєС‚РѕСЂР° РјРµРЅСЋ (API)
    MENU_LAYOUT_ENABLED: bool = False  # Р’РєР»СЋС‡РёС‚СЊ СѓРїСЂР°РІР»РµРЅРёРµ РјРµРЅСЋ С‡РµСЂРµР· API

    # РќР°СЃС‚СЂРѕР№РєРё РјРѕРЅРёС‚РѕСЂРёРЅРіР° С‚СЂР°С„РёРєР°
    TRAFFIC_MONITORING_ENABLED: bool = False  # Р“Р»РѕР±Р°Р»СЊРЅС‹Р№ РїРµСЂРµРєР»СЋС‡Р°С‚РµР»СЊ (РґР»СЏ РѕР±СЂР°С‚РЅРѕР№ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚Рё)
    TRAFFIC_THRESHOLD_GB_PER_DAY: float = 10.0  # РџРѕСЂРѕРі С‚СЂР°С„РёРєР° РІ Р“Р‘ Р·Р° СЃСѓС‚РєРё (РґР»СЏ РѕР±СЂР°С‚РЅРѕР№ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚Рё)
    TRAFFIC_MONITORING_INTERVAL_HOURS: int = 24  # РРЅС‚РµСЂРІР°Р» РїСЂРѕРІРµСЂРєРё РІ С‡Р°СЃР°С… (РґР»СЏ РѕР±СЂР°С‚РЅРѕР№ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚Рё)
    SUSPICIOUS_NOTIFICATIONS_TOPIC_ID: int | None = None

    # РќРѕРІС‹Р№ РјРѕРЅРёС‚РѕСЂРёРЅРі С‚СЂР°С„РёРєР° v2
    # Р‘С‹СЃС‚СЂР°СЏ РїСЂРѕРІРµСЂРєР° (С‚РµРєСѓС‰РёР№ РёСЃРїРѕР»СЊР·РѕРІР°РЅРЅС‹Р№ С‚СЂР°С„РёРє)
    TRAFFIC_FAST_CHECK_ENABLED: bool = False
    TRAFFIC_FAST_CHECK_INTERVAL_MINUTES: int = 10  # РРЅС‚РµСЂРІР°Р» РїСЂРѕРІРµСЂРєРё РІ РјРёРЅСѓС‚Р°С…
    TRAFFIC_FAST_CHECK_THRESHOLD_GB: float = 5.0  # РџРѕСЂРѕРі РІ Р“Р‘ РґР»СЏ Р±С‹СЃС‚СЂРѕР№ РїСЂРѕРІРµСЂРєРё

    # РЎСѓС‚РѕС‡РЅР°СЏ РїСЂРѕРІРµСЂРєР° (С‚СЂР°С„РёРє Р·Р° 24 С‡Р°СЃР°)
    TRAFFIC_DAILY_CHECK_ENABLED: bool = False
    TRAFFIC_DAILY_CHECK_TIME: str = '00:00'  # Р’СЂРµРјСЏ СЃСѓС‚РѕС‡РЅРѕР№ РїСЂРѕРІРµСЂРєРё (HH:MM)
    TRAFFIC_DAILY_THRESHOLD_GB: float = 50.0  # РџРѕСЂРѕРі СЃСѓС‚РѕС‡РЅРѕРіРѕ С‚СЂР°С„РёРєР° РІ Р“Р‘

    # Р¤РёР»СЊС‚СЂР°С†РёСЏ РїРѕ СЃРµСЂРІРµСЂР°Рј (UUID РЅРѕРґ С‡РµСЂРµР· Р·Р°РїСЏС‚СѓСЋ)
    TRAFFIC_MONITORED_NODES: str = ''  # РўРѕР»СЊРєРѕ СЌС‚Рё РЅРѕРґС‹ (РїСѓСЃС‚Рѕ = РІСЃРµ)
    TRAFFIC_IGNORED_NODES: str = ''  # РСЃРєР»СЋС‡РёС‚СЊ СЌС‚Рё РЅРѕРґС‹
    TRAFFIC_EXCLUDED_USER_UUIDS: str = ''  # РСЃРєР»СЋС‡РёС‚СЊ РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№ (UUID С‡РµСЂРµР· Р·Р°РїСЏС‚СѓСЋ)

    # РџР°СЂР°Р»Р»РµР»СЊРЅРѕСЃС‚СЊ Рё РєСѓР»РґР°СѓРЅ
    TRAFFIC_CHECK_BATCH_SIZE: int = 1000  # Р Р°Р·РјРµСЂ Р±Р°С‚С‡Р° РґР»СЏ РїРѕР»СѓС‡РµРЅРёСЏ РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№
    TRAFFIC_CHECK_CONCURRENCY: int = 10  # РџР°СЂР°Р»Р»РµР»СЊРЅС‹С… Р·Р°РїСЂРѕСЃРѕРІ
    TRAFFIC_NOTIFICATION_COOLDOWN_MINUTES: int = 60  # РљСѓР»РґР°СѓРЅ СѓРІРµРґРѕРјР»РµРЅРёР№ (РјРёРЅСѓС‚С‹)
    TRAFFIC_SNAPSHOT_TTL_HOURS: int = 24  # TTL РґР»СЏ snapshot С‚СЂР°С„РёРєР° РІ Redis (С‡Р°СЃС‹)
    # РќР°СЃС‚СЂРѕР№РєРё СЃСѓС‚РѕС‡РЅС‹С… РїРѕРґРїРёСЃРѕРє
    DAILY_SUBSCRIPTIONS_ENABLED: bool = True  # Р’РєР»СЋС‡РёС‚СЊ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРѕРµ СЃРїРёСЃР°РЅРёРµ РґР»СЏ СЃСѓС‚РѕС‡РЅС‹С… С‚Р°СЂРёС„РѕРІ
    DAILY_SUBSCRIPTIONS_CHECK_INTERVAL_MINUTES: int = 30  # РРЅС‚РµСЂРІР°Р» РїСЂРѕРІРµСЂРєРё РІ РјРёРЅСѓС‚Р°С…

    AUTOPAY_WARNING_DAYS: str = '3,1'

    ENABLE_AUTOPAY: bool = False

    DEFAULT_AUTOPAY_ENABLED: bool = False
    DEFAULT_AUTOPAY_DAYS_BEFORE: int = 3
    # 0 в†’ use the tariff's shortest (cheapest) period, as before.
    # >0 в†’ autopay charges this many days each cycle by default (must be present in tariff/renewal periods).
    # Per-subscription override lives in Subscription.autopay_period_days.
    DEFAULT_AUTOPAY_PERIOD_DAYS: int = 0
    MIN_BALANCE_FOR_AUTOPAY_KOPEKS: int = 10000

    # в”Ђв”Ђ РђРЅС‚РёСЃРїР°Рј СѓРІРµРґРѕРјР»РµРЅРёР№ РѕР± РѕС€РёР±РєРµ Р°РІС‚РѕРїР»Р°С‚РµР¶Р° в”Ђв”Ђ
    # РњР°РєСЃРёРјСѓРј СѓРІРµРґРѕРјР»РµРЅРёР№ РѕР± РѕС€РёР±РєРµ СЃРїРёСЃР°РЅРёСЏ Р·Р° РћР”РРќ С†РёРєР» РїРѕРґРїРёСЃРєРё (РґРѕ СЃР»РµРґСѓСЋС‰РµРіРѕ end_date).
    # 0 вЂ” РЅРµ РѕС‚РїСЂР°РІР»СЏС‚СЊ СѓРІРµРґРѕРјР»РµРЅРёСЏ РѕР± РѕС€РёР±РєРµ РІРѕРІСЃРµ.
    AUTOPAY_FAIL_MAX_NOTIFICATIONS: int = 2
    # Р—Р° СЃРєРѕР»СЊРєРѕ С‡Р°СЃРѕРІ РґРѕ РѕРєРѕРЅС‡Р°РЅРёСЏ РїРѕРґРїРёСЃРєРё СЃР»Р°С‚СЊ В«С„РёРЅР°Р»СЊРЅРѕРµВ» РЅР°РїРѕРјРёРЅР°РЅРёРµ. 0 вЂ” Р±РµР· С„РёРЅР°Р»Р°.
    AUTOPAY_FAIL_FINAL_REMINDER_HOURS: int = 3
    # РџРµСЂРёРѕРґРёС‡РµСЃРєРёРµ РїРѕРІС‚РѕСЂС‹ РјРµР¶РґСѓ РїРµСЂРІС‹Рј Рё С„РёРЅР°Р»СЊРЅС‹Рј СѓРІРµРґРѕРјР»РµРЅРёРµРј, РєР°Р¶РґС‹Рµ N С‡Р°СЃРѕРІ
    # (legacy-СЂРµР¶РёРј). 0 вЂ” Р±РµР· РїРѕРІС‚РѕСЂРѕРІ (С‚РѕР»СЊРєРѕ РїРµСЂРІРѕРµ + С„РёРЅР°Р»СЊРЅРѕРµ).
    AUTOPAY_FAIL_REPEAT_INTERVAL_HOURS: int = 0

    SUBSCRIPTION_RENEWAL_BALANCE_THRESHOLD_KOPEKS: int = 20000

    MONITORING_INTERVAL: int = 60
    # Р–С‘СЃС‚РєРёР№ per-send С‚Р°Р№РјР°СѓС‚ (СЃРµРє) РЅР° РѕС‚РїСЂР°РІРєСѓ СѓРІРµРґРѕРјР»РµРЅРёР№ РёР· MonitoringService.
    # Р”РµС„РѕР»С‚РЅС‹Р№ session timeout aiogram = 60s; РїСЂРё РјРµРґР»РµРЅРЅРѕРј РєР°РЅР°Р»Рµ РґРѕ Telegram
    # РёР»Рё РЅРµРґРѕСЃС‚СѓРїРЅРѕРј РїРѕР»СѓС‡Р°С‚РµР»Рµ РѕРґРёРЅ send_photo/send_message Р±Р»РѕРєРёСЂСѓРµС‚ Р’Р•РЎР¬ С…РІРѕСЃС‚
    # С†РёРєР»Р° РјРѕРЅРёС‚РѕСЂРёРЅРіР° РЅР° РјРёРЅСѓС‚С‹ (РїРѕСЃР»РµРґРѕРІР°С‚РµР»СЊРЅРѕ РїРѕ РјРЅРѕРіРёРј РїРѕР»СѓС‡Р°С‚РµР»СЏРј, Р±РµР·
    # per-send Р»РѕРіРѕРІ). Р­С‚РѕС‚ С‚Р°Р№РјР°СѓС‚ РґР°С‘С‚ Р±С‹СЃС‚СЂС‹Р№ РїСЂРµРґСЃРєР°Р·СѓРµРјС‹Р№ РїСЂРµРґРµР»: РЅР° TimeoutError
    # РїРѕР»СѓС‡Р°С‚РµР»СЊ РїСЂРѕРїСѓСЃРєР°РµС‚СЃСЏ, С†РёРєР» РїСЂРѕРґРѕР»Р¶Р°РµС‚СЃСЏ.
    MONITORING_NOTIFICATION_SEND_TIMEOUT: float = 20.0
    LOW_BALANCE_ALERT_EXPIRY_DAYS: int = 3  # Only alert when subscription expires within N days
    # Months of inactivity before a user row is soft-deleted (status=DELETED).
    # 12 months is conservative вЂ” VPN users are highly seasonal (vacations,
    # business trips, geo-blocking events). Aggressive defaults were
    # mass-deleting returning users; cabinet auto-revival makes the cost of
    # raising this low. See `app/services/user_revival_service.py`.
    INACTIVE_USER_DELETE_MONTHS: int = 12

    MAINTENANCE_MODE: bool = False
    MAINTENANCE_CHECK_INTERVAL: int = 30
    MAINTENANCE_AUTO_ENABLE: bool = True
    MAINTENANCE_MONITORING_ENABLED: bool = True
    MAINTENANCE_RETRY_ATTEMPTS: int = 1
    MAINTENANCE_MESSAGE: str = 'Р’РµРґСѓС‚СЃСЏ С‚РµС…РЅРёС‡РµСЃРєРёРµ СЂР°Р±РѕС‚С‹. РЎРµСЂРІРёСЃ РІСЂРµРјРµРЅРЅРѕ РЅРµРґРѕСЃС‚СѓРїРµРЅ. РџРѕРїСЂРѕР±СѓР№С‚Рµ РїРѕР·Р¶Рµ.'

    TELEGRAM_STARS_ENABLED: bool = True
    # в‚Ѕ per 1 в­ђ. Matches Telegram's own cash-out rate (~0.95вЂ“1.0 в‚Ѕ/ as of'
    # 2026-05) so an integer-ruble top-up round-trips losslessly:
    # rubles_to_stars(150) в†’ 150 в­ђ в†’ stars_to_rubles(150) в†’ 150 в‚Ѕ.
    # The previous 1.3 default undervalued stars by ~30% (bot quoted 115 в­ђ
    # for a 150 в‚Ѕ top-up, credited only 149.50 в‚Ѕ back вЂ” a built-in
    # rounding loss visible on every payment).
    TELEGRAM_STARS_RATE_RUB: float = 1.0
    TELEGRAM_STARS_DISPLAY_NAME: str = 'Telegram Stars'

    # Telegram Login Widget (cabinet auth page)
    TELEGRAM_WIDGET_SIZE: Literal['large', 'medium', 'small'] = 'large'
    TELEGRAM_WIDGET_RADIUS: int = Field(default=8, ge=0, le=20)
    TELEGRAM_WIDGET_USERPIC: bool = True
    TELEGRAM_WIDGET_REQUEST_ACCESS: bool = True

    # Telegram Login OIDC (new system via oauth.telegram.org)
    TELEGRAM_OIDC_ENABLED: bool = False
    TELEGRAM_OIDC_CLIENT_ID: str = ''
    TELEGRAM_OIDC_CLIENT_SECRET: str = ''

    TRIBUTE_ENABLED: bool = False
    TRIBUTE_API_KEY: str | None = None
    TRIBUTE_DONATE_LINK: str | None = None
    TRIBUTE_WEBHOOK_PATH: str = '/tribute-webhook'
    TRIBUTE_WEBHOOK_HOST: str = '0.0.0.0'
    TRIBUTE_WEBHOOK_PORT: int = 8081

    YOOKASSA_ENABLED: bool = False
    YOOKASSA_DISPLAY_NAME: str = 'YooKassa'
    # HTTP socket timeouts for yookassa SDK requests. The SDK itself
    # ships with NO timeout, so a hanging YK endpoint will block a
    # worker thread forever (until TCP keep-alive eventually kills it,
    # hours later). app/services/yookassa_service.py monkey-patches
    # ApiClient.execute to pass these values to requests.Session, so
    # threads are guaranteed to unstick within ``read`` seconds.
    #
    # Read=10s catches P99.9 degradation while keeping pool-slot
    # occupancy bounded вЂ” at 4 workers, a degradation event can pin
    # the pool for at most 10s instead of 15s (33% faster recovery).
    # YK normal latency is ~500ms, so 10s read is still ~20Г— headroom.
    #
    # Operators floor at 1s вЂ” setting either to ``0`` silently falls
    # back to the default below to avoid disabling protection entirely.
    YOOKASSA_HTTP_CONNECT_TIMEOUT: int = 5
    YOOKASSA_HTTP_READ_TIMEOUT: int = 10

    # Bounded thread pool for synchronous yookassa SDK calls. Default 4
    # is a balance between burst capacity (~8 req/s normal, ~2 req/s
    # under degradation per Little's law) and memory footprint (~32MB
    # per worker stack). High-volume operators can raise this to 6-8
    # without splitting into polling vs webhook lanes вЂ” that split is
    # a separate refactor.
    YOOKASSA_MAX_CONCURRENT_REQUESTS: int = 4
    YOOKASSA_SHOP_ID: str | None = None
    YOOKASSA_SECRET_KEY: str | None = None
    YOOKASSA_RETURN_URL: str | None = None
    YOOKASSA_DEFAULT_RECEIPT_EMAIL: str | None = None
    YOOKASSA_VAT_CODE: int = 1
    YOOKASSA_SBP_ENABLED: bool = False
    YOOKASSA_PAYMENT_MODE: str = 'full_payment'
    YOOKASSA_PAYMENT_SUBJECT: str = 'service'
    YOOKASSA_WEBHOOK_PATH: str = '/yookassa-webhook'
    YOOKASSA_WEBHOOK_HOST: str = '0.0.0.0'
    YOOKASSA_WEBHOOK_PORT: int = 8082
    YOOKASSA_TRUSTED_PROXY_NETWORKS: str = ''
    YOOKASSA_MIN_AMOUNT_KOPEKS: int = 5000
    YOOKASSA_MAX_AMOUNT_KOPEKS: int = 1000000
    YOOKASSA_RECURRENT_ENABLED: bool = False
    YOOKASSA_RECURRENT_REQUIRED: bool = False
    YOOKASSA_TEST_MODE: bool = False
    SUPPORT_TOPUP_ENABLED: bool = True
    PAYMENT_VERIFICATION_AUTO_CHECK_ENABLED: bool = False
    PAYMENT_VERIFICATION_AUTO_CHECK_INTERVAL_MINUTES: int = 10

    NALOGO_ENABLED: bool = False
    NALOGO_INN: str | None = None
    NALOGO_PASSWORD: str | None = None
    NALOGO_DEVICE_ID: str | None = None
    NALOGO_STORAGE_PATH: str = './nalogo_tokens.json'
    NALOGO_PROXY_URL: str | None = None  # SOCKS proxy for nalog.ru; falls back to PROXY_URL if not set

    AUTO_PURCHASE_AFTER_TOPUP_ENABLED: bool = False

    # РћС‚РєР»СЋС‡РµРЅРёРµ РїСЂРµРІСЊСЋ СЃСЃС‹Р»РѕРє РІ СЃРѕРѕР±С‰РµРЅРёСЏС… Р±РѕС‚Р°
    DISABLE_WEB_PAGE_PREVIEW: bool = False
    ACTIVATE_BUTTON_VISIBLE: bool = False
    ACTIVATE_BUTTON_TEXT: str = 'Р°РєС‚РёРІРёСЂРѕРІР°С‚СЊ'
    PAYMENT_BALANCE_DESCRIPTION: str = 'РџРѕРїРѕР»РЅРµРЅРёРµ Р±Р°Р»Р°РЅСЃР°'
    PAYMENT_SUBSCRIPTION_DESCRIPTION: str = 'РћРїР»Р°С‚Р° РїРѕРґРїРёСЃРєРё'
    PAYMENT_SERVICE_NAME: str = 'РРЅС‚РµСЂРЅРµС‚-СЃРµСЂРІРёСЃ'
    PAYMENT_BALANCE_TEMPLATE: str = '{service_name} - {description}'
    PAYMENT_SUBSCRIPTION_TEMPLATE: str = '{service_name} - {description}'

    CRYPTOBOT_ENABLED: bool = False
    CRYPTOBOT_DISPLAY_NAME: str = 'CryptoBot'
    CRYPTOBOT_API_TOKEN: str | None = None
    CRYPTOBOT_WEBHOOK_SECRET: str | None = None
    CRYPTOBOT_BASE_URL: str = 'https://pay.crypt.bot'
    CRYPTOBOT_TESTNET: bool = False
    CRYPTOBOT_WEBHOOK_PATH: str = '/cryptobot-webhook'
    CRYPTOBOT_WEBHOOK_PORT: int = 8083
    CRYPTOBOT_DEFAULT_ASSET: str = 'USDT'
    CRYPTOBOT_ASSETS: str = 'USDT,TON,BTC,ETH'
    CRYPTOBOT_INVOICE_EXPIRES_HOURS: int = 24

    HELEKET_ENABLED: bool = False
    HELEKET_DISPLAY_NAME: str = 'Heleket Crypto'
    HELEKET_MERCHANT_ID: str | None = None
    HELEKET_API_KEY: str | None = None
    HELEKET_BASE_URL: str = 'https://api.heleket.com/v1'
    HELEKET_DEFAULT_CURRENCY: str = 'USDT'
    HELEKET_DEFAULT_NETWORK: str | None = None
    HELEKET_INVOICE_LIFETIME: int = 3600
    HELEKET_MARKUP_PERCENT: float = 0.0
    HELEKET_WEBHOOK_PATH: str = '/heleket-webhook'
    HELEKET_WEBHOOK_HOST: str = '0.0.0.0'
    HELEKET_WEBHOOK_PORT: int = 8086
    HELEKET_CALLBACK_URL: str | None = None
    HELEKET_RETURN_URL: str | None = None
    HELEKET_SUCCESS_URL: str | None = None

    MULENPAY_ENABLED: bool = False
    MULENPAY_API_KEY: str | None = None
    MULENPAY_SECRET_KEY: str | None = None
    MULENPAY_SHOP_ID: int | None = None
    MULENPAY_BASE_URL: str = 'https://mulenpay.ru/api'
    MULENPAY_WEBHOOK_PATH: str = '/mulenpay-webhook'
    MULENPAY_DISPLAY_NAME: str = 'Mulen Pay'
    MULENPAY_DESCRIPTION: str = 'РџРѕРїРѕР»РЅРµРЅРёРµ Р±Р°Р»Р°РЅСЃР°'
    MULENPAY_LANGUAGE: str = 'ru'
    MULENPAY_VAT_CODE: int = 0

    DISPLAY_NAME_RESTRICTION_ENABLED: bool = True
    DISPLAY_NAME_BANNED_KEYWORDS: str = '\n'.join(DEFAULT_DISPLAY_NAME_BANNED_KEYWORDS)
    MULENPAY_PAYMENT_SUBJECT: int = 4
    MULENPAY_PAYMENT_MODE: int = 4
    MULENPAY_MIN_AMOUNT_KOPEKS: int = 10000
    MULENPAY_MAX_AMOUNT_KOPEKS: int = 10000000
    MULENPAY_IFRAME_EXPECTED_ORIGIN: str | None = None
    MULENPAY_WEBSITE_URL: str | None = None

    PAL24_ENABLED: bool = False
    PAL24_DISPLAY_NAME: str = 'PAL24'
    PAL24_API_TOKEN: str | None = None
    PAL24_SHOP_ID: str | None = None
    PAL24_SIGNATURE_TOKEN: str | None = None
    PAL24_BASE_URL: str = 'https://pal24.pro/api/v1/'
    PAL24_WEBHOOK_PATH: str = '/pal24-webhook'
    PAL24_PAYMENT_DESCRIPTION: str = 'РџРѕРїРѕР»РЅРµРЅРёРµ Р±Р°Р»Р°РЅСЃР°'
    PAL24_MIN_AMOUNT_KOPEKS: int = 10000
    PAL24_MAX_AMOUNT_KOPEKS: int = 100000000
    PAL24_REQUEST_TIMEOUT: int = 30
    PAL24_SBP_BUTTON_TEXT: str | None = None
    PAL24_CARD_BUTTON_TEXT: str | None = None
    PAL24_SBP_BUTTON_VISIBLE: bool = True
    PAL24_CARD_BUTTON_VISIBLE: bool = True

    PLATEGA_ENABLED: bool = False
    PLATEGA_MERCHANT_ID: str | None = None
    PLATEGA_SECRET: str | None = None
    PLATEGA_DISPLAY_NAME: str = 'Platega'
    PLATEGA_BASE_URL: str = 'https://app.platega.io'
    PLATEGA_API_VERSION: str = 'v1'  # API СЃРѕР·РґР°РЅРёСЏ РїР»Р°С‚РµР¶Р°: v1 | v2
    PLATEGA_RETURN_URL: str | None = None
    PLATEGA_FAILED_URL: str | None = None
    PLATEGA_CURRENCY: str = 'RUB'
    PLATEGA_ACTIVE_METHODS: str = '2,11,12,13'
    PLATEGA_INLINE_METHODS: bool = True
    PLATEGA_MIN_AMOUNT_KOPEKS: int = 10000
    PLATEGA_MAX_AMOUNT_KOPEKS: int = 100000000
    PLATEGA_WEBHOOK_PATH: str = '/platega-webhook'
    PLATEGA_WEBHOOK_HOST: str = '0.0.0.0'
    PLATEGA_WEBHOOK_PORT: int = 8086
    PLATEGA_RECURRENT_ENABLED: bool = False  # Р РµРєСѓСЂСЂРµРЅС‚РЅС‹Рµ РЎР‘Рџ-РїРѕРґРїРёСЃРєРё Platega (Р°РІС‚РѕРїСЂРѕРґР»РµРЅРёРµ)

    WATA_ENABLED: bool = False
    WATA_DISPLAY_NAME: str = 'Wata'
    WATA_BASE_URL: str = 'https://api.wata.pro/api/h2h'
    WATA_ACCESS_TOKEN: str | None = None
    WATA_TERMINAL_PUBLIC_ID: str | None = None
    WATA_PAYMENT_DESCRIPTION: str = 'РџРѕРїРѕР»РЅРµРЅРёРµ Р±Р°Р»Р°РЅСЃР°'
    WATA_PAYMENT_TYPE: str = 'OneTime'
    WATA_SUCCESS_REDIRECT_URL: str | None = None
    WATA_FAIL_REDIRECT_URL: str | None = None
    WATA_LINK_TTL_MINUTES: int | None = None
    WATA_MIN_AMOUNT_KOPEKS: int = 10000
    WATA_MAX_AMOUNT_KOPEKS: int = 100000000
    WATA_REQUEST_TIMEOUT: int = 30
    WATA_WEBHOOK_PATH: str = '/wata-webhook'
    WATA_WEBHOOK_HOST: str = '0.0.0.0'
    WATA_WEBHOOK_PORT: int = 8085
    WATA_PUBLIC_KEY_URL: str | None = None
    WATA_PUBLIC_KEY_CACHE_SECONDS: int = 3600

    # CloudPayments
    CLOUDPAYMENTS_ENABLED: bool = False
    CLOUDPAYMENTS_DISPLAY_NAME: str = 'CloudPayments'
    CLOUDPAYMENTS_PUBLIC_ID: str | None = None
    CLOUDPAYMENTS_API_SECRET: str | None = None
    CLOUDPAYMENTS_API_URL: str = 'https://api.cloudpayments.ru'
    CLOUDPAYMENTS_WIDGET_URL: str = 'https://widget.cloudpayments.ru/show'
    CLOUDPAYMENTS_DESCRIPTION: str = 'РџРѕРїРѕР»РЅРµРЅРёРµ Р±Р°Р»Р°РЅСЃР°'
    CLOUDPAYMENTS_CURRENCY: str = 'RUB'
    CLOUDPAYMENTS_MIN_AMOUNT_KOPEKS: int = 5000
    CLOUDPAYMENTS_MAX_AMOUNT_KOPEKS: int = 10000000
    CLOUDPAYMENTS_WEBHOOK_PATH: str = '/cloudpayments-webhook'
    CLOUDPAYMENTS_WEBHOOK_HOST: str = '0.0.0.0'
    CLOUDPAYMENTS_WEBHOOK_PORT: int = 8087
    CLOUDPAYMENTS_RETURN_URL: str | None = None
    CLOUDPAYMENTS_SKIN: str = 'mini'  # mini, classic, modern
    CLOUDPAYMENTS_REQUIRE_EMAIL: bool = False
    CLOUDPAYMENTS_TEST_MODE: bool = False

    # Freekassa
    FREEKASSA_ENABLED: bool = False
    FREEKASSA_SHOP_ID: int | None = None
    FREEKASSA_API_KEY: str | None = None
    FREEKASSA_SECRET_WORD_1: str | None = None  # Р”Р»СЏ С„РѕСЂРјС‹ РѕРїР»Р°С‚С‹
    FREEKASSA_SECRET_WORD_2: str | None = None  # Р”Р»СЏ webhook
    FREEKASSA_DISPLAY_NAME: str = 'Freekassa'
    FREEKASSA_CURRENCY: str = 'RUB'
    FREEKASSA_MIN_AMOUNT_KOPEKS: int = 10000  # 100 СЂСѓР±
    FREEKASSA_MAX_AMOUNT_KOPEKS: int = 100000000  # 1 000 000 СЂСѓР±
    FREEKASSA_PAYMENT_TIMEOUT_SECONDS: int = 3600
    FREEKASSA_WEBHOOK_PATH: str = '/freekassa-webhook'
    FREEKASSA_WEBHOOK_HOST: str = '0.0.0.0'
    FREEKASSA_WEBHOOK_PORT: int = 8088
    # РЎРїРѕСЃРѕР± РѕРїР»Р°С‚С‹: None = С„РѕСЂРјР° РІС‹Р±РѕСЂР°, 42 = РѕР±С‹С‡РЅС‹Р№ РЎР‘Рџ, 44 = NSPK РЎР‘Рџ
    FREEKASSA_PAYMENT_SYSTEM_ID: int | None = None
    # РСЃРїРѕР»СЊР·РѕРІР°С‚СЊ API РґР»СЏ СЃРѕР·РґР°РЅРёСЏ Р·Р°РєР°Р·РѕРІ (РЅСѓР¶РЅРѕ РґР»СЏ NSPK РЎР‘Рџ)
    FREEKASSA_USE_API: bool = False
    # РџСѓР±Р»РёС‡РЅС‹Р№ IP СЃРµСЂРІРµСЂР° РґР»СЏ Freekassa API (РµСЃР»Рё РЅРµ Р·Р°РґР°РЅ - РѕРїСЂРµРґРµР»СЏРµС‚СЃСЏ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё)
    SERVER_PUBLIC_IP: str | None = None
    # Р Р°Р·РґРµР»СЊРЅС‹Рµ РјРµС‚РѕРґС‹ РѕРїР»Р°С‚С‹ Freekassa (РѕС‚РѕР±СЂР°Р¶Р°СЋС‚СЃСЏ РєР°Рє РѕС‚РґРµР»СЊРЅС‹Рµ РєРЅРѕРїРєРё)
    FREEKASSA_SBP_ENABLED: bool = False  # РЎР‘Рџ (QR РєРѕРґ) вЂ” i=44
    FREEKASSA_SBP_DISPLAY_NAME: str = 'РЎР‘Рџ (QR РєРѕРґ)'
    FREEKASSA_CARD_ENABLED: bool = False  # РљР°СЂС‚С‹ Р Р¤ вЂ” i=36
    FREEKASSA_CARD_DISPLAY_NAME: str = 'РљР°СЂС‚Р° Р Р¤'

    # KassaAI (api.fk.life) - РѕС‚РґРµР»СЊРЅР°СЏ РїР»Р°С‚С‘Р¶РєР°
    KASSA_AI_ENABLED: bool = False
    KASSA_AI_SHOP_ID: int | None = None
    KASSA_AI_API_KEY: str | None = None
    KASSA_AI_SECRET_WORD_2: str | None = None  # Р”Р»СЏ webhook
    KASSA_AI_DISPLAY_NAME: str = 'KassaAI'
    KASSA_AI_CURRENCY: str = 'RUB'
    KASSA_AI_MIN_AMOUNT_KOPEKS: int = 10000  # 100 СЂСѓР±
    KASSA_AI_MAX_AMOUNT_KOPEKS: int = 100000000  # 1 000 000 СЂСѓР±
    KASSA_AI_WEBHOOK_PATH: str = '/kassa-ai-webhook'
    KASSA_AI_WEBHOOK_HOST: str = '0.0.0.0'
    KASSA_AI_WEBHOOK_PORT: int = 8089
    # РЎРїРѕСЃРѕР± РѕРїР»Р°С‚С‹: 44 = РЎР‘Рџ (QR РєРѕРґ), 36 = РљР°СЂС‚С‹ Р Р¤, 43 = SberPay
    KASSA_AI_PAYMENT_SYSTEM_ID: int = 44
    # Р Р°Р·РґРµР»СЊРЅС‹Рµ РјРµС‚РѕРґС‹ РѕРїР»Р°С‚С‹ KassaAI (РѕС‚РѕР±СЂР°Р¶Р°СЋС‚СЃСЏ РєР°Рє РѕС‚РґРµР»СЊРЅС‹Рµ РєРЅРѕРїРєРё)
    KASSA_AI_SBP_ENABLED: bool = False  # РЎР‘Рџ вЂ” payment_system_id=44
    KASSA_AI_SBP_DISPLAY_NAME: str = 'РЎР‘Рџ (KassaAI)'
    KASSA_AI_CARD_ENABLED: bool = False  # РљР°СЂС‚С‹ Р Р¤ вЂ” payment_system_id=36
    KASSA_AI_CARD_DISPLAY_NAME: str = 'РљР°СЂС‚Р° (KassaAI)'
    KASSA_AI_SBERPAY_ENABLED: bool = False  # SberPay вЂ” payment_system_id=43
    KASSA_AI_SBERPAY_DISPLAY_NAME: str = 'SberPay (KassaAI)'

    # в”Ђв”Ђ Yandex Metrika offline conversions (server в†’ mc.yandex.ru/collect) в”Ђв”Ђ
    YANDEX_OFFLINE_CONV_ENABLED: bool = False
    YANDEX_OFFLINE_CONV_COUNTER_ID: str = ''
    YANDEX_OFFLINE_CONV_MEASUREMENT_SECRET: str = ''
    YANDEX_OFFLINE_CONV_START_PREFIX: str = 'utm_ya_'
    YANDEX_OFFLINE_CONV_DL: str = ''
    YANDEX_OFFLINE_CONV_DT: str = ''
    YANDEX_OFFLINE_CONV_CURRENCY: str = 'RUB'
    # Offline Conversions API (mc.yandex.ru via OAuth, yclid-keyed)
    YANDEX_OFFLINE_CONV_OAUTH_TOKEN: str = ''
    YANDEX_OFFLINE_CONV_PURCHASE_GOAL_ID: str = ''

    # в”Ђв”Ђ S2S Postback (server-to-server affiliate notifications) в”Ђв”Ђ
    S2S_POSTBACK_ENABLED: bool = False
    S2S_POSTBACK_REGISTRATION_URL: str = ''
    S2S_POSTBACK_TRIAL_URL: str = ''
    S2S_POSTBACK_PURCHASE_URL: str = ''

    # RioPay (api.riopay.online) v2.0.1
    RIOPAY_ENABLED: bool = False
    RIOPAY_API_TOKEN: str | None = None  # x-api-token header
    RIOPAY_WEBHOOK_SECRET: str | None = None  # HMAC-SHA512 РєР»СЋС‡ РґР»СЏ РІРµР±С…СѓРєРѕРІ (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ = API_TOKEN)
    RIOPAY_DISPLAY_NAME: str = 'RioPay'
    RIOPAY_CURRENCY: str = 'RUB'
    RIOPAY_MIN_AMOUNT_KOPEKS: int = 10000  # 100в‚Ѕ
    RIOPAY_MAX_AMOUNT_KOPEKS: int = 100000000  # 1 000 000в‚Ѕ
    RIOPAY_WEBHOOK_PATH: str = '/riopay-webhook'
    RIOPAY_SUCCESS_URL: str | None = None
    RIOPAY_FAIL_URL: str | None = None

    # SeverPay (severpay.io)
    SEVERPAY_ENABLED: bool = False
    SEVERPAY_MID: int | None = None  # Merchant ID
    SEVERPAY_TOKEN: str | None = None  # Secret token for HMAC-SHA256
    SEVERPAY_DISPLAY_NAME: str = 'SeverPay'
    SEVERPAY_CURRENCY: str = 'RUB'
    SEVERPAY_MIN_AMOUNT_KOPEKS: int = 10000  # 100в‚Ѕ
    SEVERPAY_MAX_AMOUNT_KOPEKS: int = 10000000  # 100 000в‚Ѕ
    SEVERPAY_WEBHOOK_PATH: str = '/severpay-webhook'
    SEVERPAY_RETURN_URL: str | None = None
    SEVERPAY_LIFETIME: int = 1440  # minutes, 30-4320

    # Apple In-App Purchase
    APPLE_IAP_ENABLED: bool = False
    APPLE_IAP_KEY_ID: str | None = None
    APPLE_IAP_ISSUER_ID: str | None = None
    APPLE_IAP_BUNDLE_ID: str = 'com.app.client'
    APPLE_IAP_APP_APPLE_ID: int | None = None
    APPLE_IAP_PRIVATE_KEY: str | None = None  # .p8 key contents (PEM)
    APPLE_IAP_PRIVATE_KEY_PATH: str | None = None  # Alternative: path to .p8 file
    APPLE_IAP_ENVIRONMENT: str = 'Production'  # 'Sandbox' or 'Production'
    APPLE_IAP_WEBHOOK_PATH: str = '/apple-iap-webhook'
    APPLE_IAP_ROOT_CERTS_PATHS: str = ''  # Comma-separated Apple root certificate files for SignedDataVerifier
    APPLE_IAP_ENABLE_ONLINE_CERT_CHECKS: bool = True
    APPLE_IAP_ALLOW_SANDBOX_ON_PRODUCTION: bool = False
    APPLE_IAP_PURCHASE_RATE_LIMIT_PER_MINUTE: int = 10
    APPLE_IAP_PURCHASE_FAILURE_LIMIT_PER_HOUR: int = 20
    APPLE_IAP_RATE_LIMIT_FAIL_OPEN: bool = False
    APPLE_IAP_PRODUCTS: str = (
        '{"com.app.client.topup.100":10000,"com.app.client.topup.300":30000,"com.app.client.topup.500":50000}'
    )

    # PayPear (paypear.ru)
    PAYPEAR_ENABLED: bool = False
    PAYPEAR_SHOP_ID: str | None = None
    PAYPEAR_SECRET_KEY: str | None = None
    PAYPEAR_DISPLAY_NAME: str = 'PayPear'
    PAYPEAR_CURRENCY: str = 'RUB'
    PAYPEAR_MIN_AMOUNT_KOPEKS: int = 10000  # 100в‚Ѕ
    PAYPEAR_MAX_AMOUNT_KOPEKS: int = 10000000  # 100 000в‚Ѕ
    PAYPEAR_WEBHOOK_PATH: str = '/paypear-webhook'
    PAYPEAR_RETURN_URL: str | None = None
    PAYPEAR_PAYMENT_METHOD: str = 'sbp'  # bank_card, sbp, sberpay, tpay

    # RollyPay (rollypay.io)
    ROLLYPAY_ENABLED: bool = False
    ROLLYPAY_API_KEY: str | None = None  # X-API-Key header
    ROLLYPAY_SIGNING_SECRET: str | None = None  # HMAC webhook verification
    ROLLYPAY_DISPLAY_NAME: str = 'RollyPay'
    ROLLYPAY_CURRENCY: str = 'RUB'
    ROLLYPAY_MIN_AMOUNT_KOPEKS: int = 10000  # 100в‚Ѕ
    ROLLYPAY_MAX_AMOUNT_KOPEKS: int = 10000000  # 100 000в‚Ѕ
    ROLLYPAY_WEBHOOK_PATH: str = '/rollypay-webhook'
    ROLLYPAY_RETURN_URL: str | None = None

    # Overpay (pay.overpay.io)
    OVERPAY_ENABLED: bool = False
    OVERPAY_API_URL: str = 'https://api.overpay.io'
    OVERPAY_USERNAME: str | None = None
    OVERPAY_PASSWORD: str | None = None
    OVERPAY_PROJECT_ID: str | None = None
    OVERPAY_P12_PATH: str | None = None
    OVERPAY_P12_PASSPHRASE: str | None = None
    OVERPAY_DISPLAY_NAME: str = 'Overpay'
    OVERPAY_CURRENCY: str = 'RUB'
    OVERPAY_MIN_AMOUNT_KOPEKS: int = 10000
    OVERPAY_MAX_AMOUNT_KOPEKS: int = 10000000
    OVERPAY_WEBHOOK_PATH: str = '/overpay-webhook'
    OVERPAY_RETURN_URL: str | None = None
    OVERPAY_LIFETIME_MINUTES: int = 1440
    OVERPAY_PAYMENT_METHODS: str = 'card,fps'
    OVERPAY_SBP_TERMINAL_ID: str | None = None
    OVERPAY_CARD_TERMINAL_ID: str | None = None
    OVERPAY_INT_TERMINAL_ID: str | None = None
    OVERPAY_SBP_DIRECT_QR: bool = False
    OVERPAY_INT_ENABLED: bool = False
    OVERPAY_INT_MIN_EUR: float = 5.0
    OVERPAY_RUB_PER_EUR: float = 0.0
    OVERPAY_SERVER_IP: str | None = None

    # AuraPay (aurapay.tech)
    AURAPAY_ENABLED: bool = False
    AURAPAY_API_KEY: str | None = None  # X-ApiKey header
    AURAPAY_SHOP_ID: str | None = None  # X-ShopId header (UUID)
    AURAPAY_SECRET_KEY: str | None = None  # Secret key #2 for webhook HMAC
    AURAPAY_DISPLAY_NAME: str = 'AuraPay'
    AURAPAY_CURRENCY: str = 'RUB'
    AURAPAY_MIN_AMOUNT_KOPEKS: int = 10000  # 100в‚Ѕ
    AURAPAY_MAX_AMOUNT_KOPEKS: int = 10000000  # 100 000в‚Ѕ
    AURAPAY_WEBHOOK_PATH: str = '/aurapay-webhook'
    AURAPAY_RETURN_URL: str | None = None
    AURAPAY_PAYMENT_LIFETIME_MINUTES: int = 60
    AURAPAY_SBP_ENABLED: bool = False
    AURAPAY_SBP_DISPLAY_NAME: str = 'РЎР‘Рџ (AuraPay)'
    AURAPAY_CARD_ENABLED: bool = False
    AURAPAY_CARD_DISPLAY_NAME: str = 'РљР°СЂС‚Р° (AuraPay)'

    # Antilopay (lk.antilopay.com)
    ANTILOPAY_ENABLED: bool = False
    ANTILOPAY_SECRET_ID: str | None = None
    ANTILOPAY_PRIVATE_KEY: str | None = None
    ANTILOPAY_PUBLIC_KEY: str | None = None
    ANTILOPAY_PROJECT_ID: str | None = None
    ANTILOPAY_DISPLAY_NAME: str = 'Antilopay'
    ANTILOPAY_PRODUCT_NAME: str = 'VPN РїРѕРґРїРёСЃРєР°'
    ANTILOPAY_PRODUCT_TYPE: str = 'services'
    ANTILOPAY_CURRENCY: str = 'RUB'
    ANTILOPAY_MIN_AMOUNT_KOPEKS: int = 10000  # 100в‚Ѕ
    ANTILOPAY_MAX_AMOUNT_KOPEKS: int = 10000000  # 100 000в‚Ѕ
    ANTILOPAY_WEBHOOK_PATH: str = '/antilopay-webhook'
    ANTILOPAY_RETURN_URL: str | None = None
    ANTILOPAY_PAYMENT_LIFETIME_MINUTES: int = 60
    ANTILOPAY_SBP_ENABLED: bool = False
    ANTILOPAY_SBP_DISPLAY_NAME: str = 'РЎР‘Рџ (Antilopay)'
    ANTILOPAY_CARD_ENABLED: bool = False
    ANTILOPAY_CARD_DISPLAY_NAME: str = 'РљР°СЂС‚Р° (Antilopay)'
    # Antilopay С‚СЂРµР±СѓРµС‚ РїРѕРґС‚РІРµСЂРґРёС‚СЊ РІР»Р°РґРµРЅРёРµ СЃР°Р№С‚РѕРј РѕРґРЅРёРј РёР· РґРІСѓС… СЃРїРѕСЃРѕР±РѕРІ:
    #   (1) META-С‚РµРіРѕРј `<meta name="apay-tag" content="...">` РІ <head> РіР»Р°РІРЅРѕР№ СЃС‚СЂР°РЅРёС†С‹;
    #   (2) С„Р°Р№Р»РѕРј `apay-meta-file.txt` РІ РєРѕСЂРЅРµ СЃР°Р№С‚Р°.
    # РљР°Р±РёРЅРµС‚ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РѕС‚СЂРµРЅРґРµСЂРёС‚ meta-С‚РµРі Рё РѕС‚РґР°СЃС‚ С‚РµРєСЃС‚РѕРІС‹Р№ С„Р°Р№Р», РµСЃР»Рё
    # СЃСЋРґР° РїРѕР»РѕР¶РёС‚СЊ РІС‹РґР°РЅРЅРѕРµ Antilopay Р·РЅР°С‡РµРЅРёРµ (СЃРј. lk.antilopay.com в†’ РџСЂРѕРµРєС‚ в†’
    # Р’РµСЂРёС„РёРєР°С†РёСЏ). РџСѓСЃС‚Р°СЏ СЃС‚СЂРѕРєР°/None вЂ” С„РёС‡Р° РѕС‚РєР»СЋС‡РµРЅР°.
    ANTILOPAY_APAY_VERIFICATION_TAG: str | None = None

    ANTILOPAY_SBERPAY_ENABLED: bool = False
    ANTILOPAY_SBERPAY_DISPLAY_NAME: str = 'SberPay (Antilopay)'

    # Jupiter (FPGate P2P v2.1, app.juppiter.tech)
    JUPITER_ENABLED: bool = False
    JUPITER_TOKEN: str | None = None
    JUPITER_SECRET: str | None = None
    JUPITER_BASE_URL: str = 'https://app.juppiter.tech'
    JUPITER_METHOD_ID: str | None = None
    JUPITER_METHOD_DESCRIPTION: str = 'SBP'
    JUPITER_DISPLAY_NAME: str = 'Jupiter'
    JUPITER_CURRENCY: str = 'RUB'
    JUPITER_MIN_AMOUNT_KOPEKS: int = 10000  # 100в‚Ѕ
    JUPITER_MAX_AMOUNT_KOPEKS: int = 10000000  # 100 000в‚Ѕ
    JUPITER_WEBHOOK_PATH: str = '/jupiter-webhook'
    JUPITER_RETURN_URL: str | None = None
    JUPITER_PAYMENT_LIFETIME_MINUTES: int = 60
    JUPITER_FALLBACK_EMAIL: str = 'user@vpn.bot'
    JUPITER_FALLBACK_PHONE: str = '0000000000'
    JUPITER_FALLBACK_NAME: str = 'User'
    JUPITER_SBP_ENABLED: bool = False
    JUPITER_SBP_DISPLAY_NAME: str = 'РЎР‘Рџ (Jupiter)'

    # Donut (Donut P2P, gw.donut.business)
    DONUT_ENABLED: bool = False
    DONUT_TOKEN: str | None = None
    DONUT_SECRET: str | None = None
    DONUT_BASE_URL: str = 'https://gw.donut.business'
    DONUT_METHOD_ID: str | None = None
    DONUT_DISPLAY_NAME: str = 'Donut'
    DONUT_CURRENCY: str = 'RUB'
    DONUT_MIN_AMOUNT_KOPEKS: int = 10000  # 100в‚Ѕ
    DONUT_MAX_AMOUNT_KOPEKS: int = 10000000  # 100 000в‚Ѕ
    DONUT_WEBHOOK_PATH: str = '/donut-webhook'
    DONUT_RETURN_URL: str | None = None
    DONUT_PAYMENT_LIFETIME_MINUTES: int = 60
    # Sub-РјРµС‚РѕРґС‹ Donut (description РІ PayIn Р·Р°РїСЂРѕСЃРµ)
    DONUT_CARD_ENABLED: bool = False
    DONUT_CARD_DISPLAY_NAME: str = 'РљР°СЂС‚Р° (Donut)'
    DONUT_SBP_ENABLED: bool = False
    DONUT_SBP_DISPLAY_NAME: str = 'РЎР‘Рџ (Donut)'
    DONUT_SBP_QR_ENABLED: bool = False
    DONUT_SBP_QR_DISPLAY_NAME: str = 'РЎР‘Рџ QR (Donut)'

    # Lava (Lava Business API, api.lava.ru)
    LAVA_ENABLED: bool = False
    LAVA_BASE_URL: str = 'https://api.lava.ru'
    LAVA_SHOP_ID: str | None = None  # UUID РїСЂРѕРµРєС‚Р°
    LAVA_SECRET_KEY: str | None = None  # secret_key вЂ” РґР»СЏ РїРѕРґРїРёСЃРё Р·Р°РїСЂРѕСЃРѕРІ
    LAVA_WEBHOOK_SECRET: str | None = None  # secret_key_2 вЂ” РґР»СЏ РїСЂРѕРІРµСЂРєРё РїРѕРґРїРёСЃРё webhook

    # Р РµРєСѓСЂСЂРµРЅС‚РЅС‹Рµ РїРѕРґРїРёСЃРєРё Lava. РџРѕРґРїРёСЃРєР° РѕС„РѕСЂРјР»СЏРµС‚СЃСЏ РЅР° РџР РћР”РЈРљРў РёР· РєР°Р±РёРЅРµС‚Р° Lava
    # (С†РµРЅР° Рё РїРµСЂРёРѕРґ Р·Р°РґР°РЅС‹ С‚Р°Рј), РїРѕСЌС‚РѕРјСѓ С‚Р°СЂРёС„Сѓ РЅСѓР¶РЅРѕ РїСЂРѕСЃС‚Р°РІРёС‚СЊ lava_product_id.
    LAVA_RECURRENT_ENABLED: bool = False
    LAVA_DISPLAY_NAME: str = 'Lava'
    LAVA_CURRENCY: str = 'RUB'
    LAVA_MIN_AMOUNT_KOPEKS: int = 10000  # 100в‚Ѕ
    LAVA_MAX_AMOUNT_KOPEKS: int = 10000000  # 100 000в‚Ѕ
    LAVA_WEBHOOK_PATH: str = '/lava-webhook'
    LAVA_RETURN_URL: str | None = None
    LAVA_PAYMENT_LIFETIME_MINUTES: int = 60  # РјР°РєСЃ 7200 РјРёРЅСѓС‚ (5 РґРЅРµР№)
    # Sub-РјРµС‚РѕРґС‹ Lava (С„РёР»СЊС‚СЂ С‡РµСЂРµР· includeService/excludeService РЅР° СЃС‚РѕСЂРѕРЅРµ Lava)
    LAVA_CARD_ENABLED: bool = False
    LAVA_CARD_DISPLAY_NAME: str = 'РљР°СЂС‚Р° (Lava)'
    LAVA_SBP_ENABLED: bool = False
    LAVA_SBP_DISPLAY_NAME: str = 'РЎР‘Рџ (Lava)'

    # cisPay (H2H merchant API, api.cispay.app)
    CISPAY_ENABLED: bool = False
    CISPAY_SHOP_ID: str | None = None  # X-Shop-ID вЂ” UUID РјР°РіР°Р·РёРЅР°
    CISPAY_API_KEY: str | None = None  # X-Api-Key вЂ” СЃРµРєСЂРµС‚РЅС‹Р№ РєР»СЋС‡ (cis_sec_...)
    CISPAY_BASE_URL: str = 'https://api.cispay.app'
    CISPAY_DISPLAY_NAME: str = 'CisPay'
    CISPAY_CURRENCY: str = 'RUB'
    CISPAY_MIN_AMOUNT_KOPEKS: int = 10000  # 100в‚Ѕ
    CISPAY_MAX_AMOUNT_KOPEKS: int = 10000000  # 100 000в‚Ѕ
    CISPAY_WEBHOOK_PATH: str = '/cispay-webhook'
    # РЎС‡С‘С‚ cisPay Р¶РёРІС‘С‚ 30 РјРёРЅСѓС‚, РїРѕСЃР»Рµ С‡РµРіРѕ РїРµСЂРµС…РѕРґРёС‚ РІ EXPIRED РЅР° СЃС‚РѕСЂРѕРЅРµ РїСЂРѕРІР°Р№РґРµСЂР°
    CISPAY_PAYMENT_LIFETIME_MINUTES: int = 30
    # Sub-РјРµС‚РѕРґС‹ cisPay (payment_method РІ Р·Р°РїСЂРѕСЃРµ СЃРѕР·РґР°РЅРёСЏ РїР»Р°С‚РµР¶Р°)
    CISPAY_CARD_ENABLED: bool = False
    CISPAY_CARD_DISPLAY_NAME: str = 'РљР°СЂС‚Р° (CisPay)'
    CISPAY_SBP_ENABLED: bool = False
    CISPAY_SBP_DISPLAY_NAME: str = 'РЎР‘Рџ (CisPay)'

    # Etoplatezhi (paymentpage.etoplatezhi.ru)
    ETOPLATEZHI_ENABLED: bool = False
    ETOPLATEZHI_PROJECT_ID: int | None = None
    ETOPLATEZHI_SECRET_KEY: str | None = None
    ETOPLATEZHI_DISPLAY_NAME: str = 'Etoplatezhi'
    ETOPLATEZHI_CURRENCY: str = 'RUB'
    ETOPLATEZHI_MIN_AMOUNT_KOPEKS: int = 10000  # 100в‚Ѕ
    ETOPLATEZHI_MAX_AMOUNT_KOPEKS: int = 10000000  # 100 000в‚Ѕ
    ETOPLATEZHI_WEBHOOK_PATH: str = '/etoplatezhi-webhook'
    ETOPLATEZHI_RETURN_URL: str | None = None
    ETOPLATEZHI_PAYMENT_LIFETIME_MINUTES: int = 60
    ETOPLATEZHI_SBP_ENABLED: bool = False
    ETOPLATEZHI_SBP_DISPLAY_NAME: str = 'РЎР‘Рџ (Etoplatezhi)'
    ETOPLATEZHI_CARD_ENABLED: bool = False
    ETOPLATEZHI_CARD_DISPLAY_NAME: str = 'РљР°СЂС‚Р° (Etoplatezhi)'

    MAIN_MENU_MODE: str = 'default'  # 'default' | 'cabinet'
    # Rich-РјРµРЅСЋ (Bot API 10.1): РіР»Р°РІРЅРѕРµ РјРµРЅСЋ СЃРѕР±РёСЂР°РµС‚СЃСЏ rich-СЃРѕРѕР±С‰РµРЅРёРµРј СЃ С‚РµРјРё Р¶Рµ
    # РєРЅРѕРїРєР°РјРё (reply_markup СЃРѕС…СЂР°РЅСЏРµС‚СЃСЏ). РўСЂРµР±СѓРµС‚ Bot API 10.1+; РїСЂРё РЅРµРґРѕСЃС‚СѓРїРЅРѕСЃС‚Рё
    # Р±РѕС‚ СЃР°Рј РѕС‚РєР°С‚С‹РІР°РµС‚СЃСЏ РЅР° РєР»Р°СЃСЃРёС‡РµСЃРєРёР№ СЂРµРЅРґРµСЂ РґРѕ СЂРµСЃС‚Р°СЂС‚Р°.
    MAIN_MENU_RICH_ENABLED: bool = False
    # Р­С„С„РµРєС‚ СЃРѕРѕР±С‰РµРЅРёСЏ РїСЂРё РѕС‚РїСЂР°РІРєРµ rich-РјРµРЅСЋ (РїСѓСЃС‚Р°СЏ СЃС‚СЂРѕРєР° вЂ” Р±РµР· СЌС„С„РµРєС‚Р°).
    MAIN_MENU_RICH_EFFECT_ID: str = ''
    # РџСѓР±Р»РёС‡РЅС‹Р№ HTTPS-URL Р»РѕРіРѕС‚РёРїР° РІ С€Р°РїРєРµ rich-РјРµРЅСЋ. РџСѓСЃС‚Рѕ вЂ” Р°РІС‚Рѕ-СЂРµР¶РёРј (webhook+LOGO_FILE);
    # "none" вЂ” rich-РјРµРЅСЋ Р±РµР· Р»РѕРіРѕС‚РёРїР°.
    MAIN_MENU_RICH_LOGO_URL: str = ''
    # РЎРІРѕСЂР°С‡РёРІР°С‚СЊ С‚Р°Р±Р»РёС†Сѓ РїРѕРґРїРёСЃРѕРє rich-РјРµРЅСЋ РІ СЂР°СЃРєСЂС‹РІР°РµРјС‹Р№ Р±Р»РѕРє РїСЂРё >1 РїРѕРґРїРёСЃРєРµ.
    MAIN_MENU_RICH_SUBSCRIPTIONS_COLLAPSIBLE: bool = True
    # РЎС‚РёР»СЊ РєРЅРѕРїРѕРє Cabinet: primary (СЃРёРЅРёР№), success (Р·РµР»С‘РЅС‹Р№), danger (РєСЂР°СЃРЅС‹Р№), '' (РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ РґР»СЏ РєР°Р¶РґРѕР№ СЃРµРєС†РёРё)
    CABINET_BUTTON_STYLE: str = ''
    CONNECT_BUTTON_MODE: str = 'miniapp_subscription'
    MINIAPP_CUSTOM_URL: str = ''
    # РљРЅРѕРїРєР° В«РњРµРЅСЋВ» Telegram РЅР° РѕС‚РєСЂС‹С‚РёРµ РІРµР±-РєР°Р±РёРЅРµС‚Р° (WebApp). РџСѓСЃС‚РѕР№ URL вЂ”
    # РїР°РґР°РµС‚ РЅР° MINIAPP_CUSTOM_URL; СЂР°Р±РѕС‚Р°РµС‚ С‚РѕР»СЊРєРѕ СЃ https.
    MENU_BUTTON_WEBAPP_ENABLED: bool = False
    MENU_BUTTON_WEBAPP_URL: str = ''
    MENU_BUTTON_WEBAPP_TEXT: str = 'РљР°Р±РёРЅРµС‚'
    MINIAPP_STATIC_PATH: str = 'miniapp'
    # РљРѕСЂРѕС‚РєРѕРµ РёРјСЏ Telegram Mini App (BotFather в†’ /newapp), РЅР°РїСЂ. 'cabinet'.
    # РќСѓР¶РЅРѕ С‚РѕР»СЊРєРѕ РґР»СЏ РґРёРїР»РёРЅРєРѕРІ t.me/<bot>/<app>?startapp=вЂ¦ РєРѕС‚РѕСЂС‹Рµ РѕС‚РєСЂС‹РІР°СЋС‚
    # РєР°Р±РёРЅРµС‚ РёР· Р“Р РЈРџРџРћР’Р«РҐ С‡Р°С‚РѕРІ (web_app-РєРЅРѕРїРєРё РІ РіСЂСѓРїРїР°С… РЅРµ СЂР°Р±РѕС‚Р°СЋС‚). Р’ Р»РёС‡РєРµ
    # РґРѕСЃС‚Р°С‚РѕС‡РЅРѕ MINIAPP_CUSTOM_URL. РџСѓСЃС‚Рѕ в†’ РІ РіСЂСѓРїРїР°С… РєРЅРѕРїРєР° РєР°Р±РёРЅРµС‚Р° РЅРµ СЃС‚СЂРѕРёС‚СЃСЏ.
    MINIAPP_APP_SHORT_NAME: str = ''

    # Media upload settings (news article images/videos)
    MEDIA_UPLOAD_DIR: str = './uploads'
    MEDIA_MAX_IMAGE_SIZE_MB: int = 10
    MEDIA_MAX_VIDEO_SIZE_MB: int = 50
    MEDIA_IMAGE_MAX_DIMENSION: int = 2048
    MEDIA_JPEG_QUALITY: int = 85
    MINIAPP_PURCHASE_URL: str = ''
    MINIAPP_SERVICE_NAME_EN: str = 'RemnaWave VPN'
    MINIAPP_SERVICE_NAME_RU: str = 'RemnaWave VPN'
    MINIAPP_SERVICE_DESCRIPTION_EN: str = 'Secure & Fast Connection'
    MINIAPP_SERVICE_DESCRIPTION_RU: str = 'Р‘РµР·РѕРїР°СЃРЅРѕРµ Рё Р±С‹СЃС‚СЂРѕРµ РїРѕРґРєР»СЋС‡РµРЅРёРµ'
    CONNECT_BUTTON_HAPP_DOWNLOAD_ENABLED: bool = False
    HAPP_CRYPTOLINK_REDIRECT_TEMPLATE: str | None = None
    HAPP_DOWNLOAD_LINK_IOS: str | None = None
    HAPP_DOWNLOAD_LINK_ANDROID: str | None = None
    HAPP_DOWNLOAD_LINK_MACOS: str | None = None
    HAPP_DOWNLOAD_LINK_WINDOWS: str | None = None
    HAPP_DOWNLOAD_LINK_PC: str | None = None
    HIDE_SUBSCRIPTION_LINK: bool = False
    ENABLE_LOGO_MODE: bool = True
    LOGO_FILE: str = 'vpn_logo.png'
    SKIP_RULES_ACCEPT: bool = False
    SKIP_REFERRAL_CODE: bool = False

    DEFAULT_LANGUAGE: str = 'ru'
    AVAILABLE_LANGUAGES: str = 'ru,en,ua,zh,fa'
    LANGUAGE_SELECTION_ENABLED: bool = True

    PRIVACY_POLICY_DISPLAY_MODE: str = 'both'
    PUBLIC_OFFER_DISPLAY_MODE: str = 'both'
    SERVICE_RULES_DISPLAY_MODE: str = 'both'
    FAQ_DISPLAY_MODE: str = 'both'
    RECURRENT_PAYMENTS_DISPLAY_MODE: str = 'both'

    # РўСЂРµР±РѕРІР°С‚СЊ РіР°Р»РѕС‡РєСѓ СЃРѕРіР»Р°СЃРёСЏ СЃ СЋСЂ. РґРѕРєСѓРјРµРЅС‚Р°РјРё РїСЂРё РїРµСЂРІРѕР№ Р°РІС‚РѕСЂРёР·Р°С†РёРё
    # РІ РєР°Р±РёРЅРµС‚Рµ. РљР»СЋС‡РµРІРѕРµ РїСЂР°РІРёР»Рѕ: РіР°Р»РѕС‡РєР° РІРѕР·РјРѕР¶РЅР° С‚РѕР»СЊРєРѕ РґР»СЏ РґРѕРєСѓРјРµРЅС‚Р°,
    # РєРѕС‚РѕСЂС‹Р№ РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃРїРѕСЃРѕР±РµРЅ РѕС‚РєСЂС‹С‚СЊ (РЅРµ РІС‹РєР»СЋС‡РµРЅ Рё РЅРµ СЃРєСЂС‹С‚ РёР· РІРµР±Р°),
    # РёРЅР°С‡Рµ СѓСЃС‚Р°РЅРѕРІРєР° Р±РµР· Р·Р°РїРѕР»РЅРµРЅРЅРѕР№ РѕС„РµСЂС‚С‹ Р·Р°Р±Р»РѕРєРёСЂРѕРІР°Р»Р° Р±С‹ РІС…РѕРґ РІСЃРµРј.
    CABINET_REQUIRE_LEGAL_CONSENT: bool = True
    # True - РіР°Р»РѕС‡РєРё РїРѕРєР°Р·С‹РІР°СЋС‚СЃСЏ СѓР¶Рµ РѕС‚РјРµС‡РµРЅРЅС‹РјРё (РєР»РёРµРЅС‚ СЃР°Рј РїРѕРґС‚РІРµСЂР¶РґР°РµС‚).
    CABINET_LEGAL_CONSENT_PRECHECKED: bool = False

    # РћРєСЂСѓРіР»РµРЅРёРµ С†РµРЅ РїСЂРё РѕС‚РѕР±СЂР°Р¶РµРЅРёРё (в‰¤50 РєРѕРї РІРЅРёР·, >50 РєРѕРї РІРІРµСЂС…)
    PRICE_ROUNDING_ENABLED: bool = True

    LOG_LEVEL: str = 'INFO'
    LOG_FILE: str = 'logs/bot.log'
    LOG_COLORS: bool = True  # ANSI-С†РІРµС‚Р° РІ РєРѕРЅСЃРѕР»Рё (false РґР»СЏ plain-text РІС‹РІРѕРґР°)

    # === Log Rotation Settings ===
    LOG_ROTATION_ENABLED: bool = False  # РџРѕ СѓРјРѕР»С‡Р°РЅРёСЋ СЃС‚Р°СЂРѕРµ РїРѕРІРµРґРµРЅРёРµ
    LOG_ROTATION_TIME: str = '00:00'  # Р’СЂРµРјСЏ СЂРѕС‚Р°С†РёРё (HH:MM)
    LOG_ROTATION_KEEP_DAYS: int = 7  # РҐСЂР°РЅРёС‚СЊ Р°СЂС…РёРІС‹ N РґРЅРµР№
    LOG_ROTATION_COMPRESS: bool = True  # РЎР¶РёРјР°С‚СЊ Р°СЂС…РёРІС‹ gzip
    LOG_ROTATION_SEND_TO_TELEGRAM: bool = False  # РћС‚РїСЂР°РІР»СЏС‚СЊ РІ РєР°РЅР°Р»
    LOG_ROTATION_CHAT_ID: str | None = None  # РљР°РЅР°Р» РґР»СЏ Р»РѕРіРѕРІ (РёР»Рё BACKUP_SEND_CHAT_ID)
    LOG_ROTATION_TOPIC_ID: int | None = None  # РўРѕРїРёРє РІ РєР°РЅР°Р»Рµ

    # РџСѓС‚Рё Рє Р»РѕРі-С„Р°Р№Р»Р°Рј (РїСЂРё LOG_ROTATION_ENABLED=true)
    LOG_DIR: str = 'logs'
    LOG_INFO_FILE: str = 'info.log'
    LOG_WARNING_FILE: str = 'warning.log'
    LOG_ERROR_FILE: str = 'error.log'
    LOG_PAYMENTS_FILE: str = 'payments.log'

    # === User action log (cabinet activity timeline) ===
    USER_ACTION_LOG_ENABLED: bool = True
    USER_ACTION_LOG_RETENTION_DAYS: int = 90

    # === Ban Notification Messages ===

    # РЎРѕРѕР±С‰РµРЅРёРµ Рѕ Р±Р»РѕРєРёСЂРѕРІРєРµ Р·Р° РїСЂРµРІС‹С€РµРЅРёРµ Р»РёРјРёС‚Р° СѓСЃС‚СЂРѕР№СЃС‚РІ
    # РџРµСЂРµРјРµРЅРЅС‹Рµ: {ip_count}, {limit}, {ban_minutes}, {node_info}
    BAN_MSG_PUNISHMENT: str = (
        '<b>РђРљРљРђРЈРќРў Р—РђР‘Р›РћРљРР РћР’РђРќ</b>\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n\n'
        '<b>РџСЂРёС‡РёРЅР°:</b> РџСЂРµРІС‹С€РµРЅ Р»РёРјРёС‚ СѓСЃС‚СЂРѕР№СЃС‚РІ\n'
        '{node_info}\n'
        '<b>Р”РµС‚Р°Р»Рё РЅР°СЂСѓС€РµРЅРёСЏ:</b>\n'
        'в”њ  РЈСЃС‚СЂРѕР№СЃС‚РІ РїРѕРґРєР»СЋС‡РµРЅРѕ: <b>{ip_count}</b>\n'
        'в”њ  Р Р°Р·СЂРµС€РµРЅРѕ РїРѕ С‚Р°СЂРёС„Сѓ: <b>{limit}</b>\n'
        'в””  Р’СЂРµРјСЏ Р±Р»РѕРєРёСЂРѕРІРєРё: <b>{ban_minutes} РјРёРЅ</b>\n\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n'
        '<b>Р§С‚Рѕ РґРµР»Р°С‚СЊ:</b>\n'
        '1. РћС‚РєР»СЋС‡РёС‚Рµ Р»РёС€РЅРёРµ СѓСЃС‚СЂРѕР№СЃС‚РІР° РѕС‚ VPN\n'
        '2. Р”РѕР¶РґРёС‚РµСЃСЊ РѕРєРѕРЅС‡Р°РЅРёСЏ Р±Р»РѕРєРёСЂРѕРІРєРё\n'
        '3. РџРѕРґРєР»СЋС‡РёС‚РµСЃСЊ Р·Р°РЅРѕРІРѕ\n\n'
        'Р”РѕСЃС‚СѓРї РІРѕСЃСЃС‚Р°РЅРѕРІРёС‚СЃСЏ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё'
    )

    BAN_MSG_REVOKE: str = (
        'рџ”‘ <b>РљР›Р®Р§Р Р”РћРЎРўРЈРџРђ РћР‘РќРћР’Р›Р•РќР«</b>\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n\n'
        'вќЊ <b>РџСЂРёС‡РёРЅР°:</b> РџСЂРµРІС‹С€РµРЅ Р»РёРјРёС‚ СѓСЃС‚СЂРѕР№СЃС‚РІ\n'
        '{node_info}\n'
        'рџ“Љ <b>Р”РµС‚Р°Р»Рё РЅР°СЂСѓС€РµРЅРёСЏ:</b>\n'
        'в”њ рџ“± РЈСЃС‚СЂРѕР№СЃС‚РІ РїРѕРґРєР»СЋС‡РµРЅРѕ: <b>{ip_count}</b>\n'
        'в”” рџ“‹ Р Р°Р·СЂРµС€РµРЅРѕ РїРѕ С‚Р°СЂРёС„Сѓ: <b>{limit}</b>\n\n'
        'РћС‚РєР»СЋС‡РёС‚Рµ Р»РёС€РЅРёРµ СѓСЃС‚СЂРѕР№СЃС‚РІР° Рё Р·Р°РЅРѕРІРѕ РїРѕР»СѓС‡РёС‚Рµ Р°РєС‚СѓР°Р»СЊРЅС‹Р№ РєР»СЋС‡ РїРѕРґРєР»СЋС‡РµРЅРёСЏ РІ Р±РѕС‚Рµ.'
    )

    # РЎРѕРѕР±С‰РµРЅРёРµ Рѕ СЂР°Р·Р±Р»РѕРєРёСЂРѕРІРєРµ
    BAN_MSG_ENABLED: str = (
        '<b>РђРљРљРђРЈРќРў Р РђР—Р‘Р›РћРљРР РћР’РђРќ</b>\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n\n'
        'Р’Р°С€ Р°РєРєР°СѓРЅС‚ СѓСЃРїРµС€РЅРѕ СЂР°Р·Р±Р»РѕРєРёСЂРѕРІР°РЅ!\n\n'
        'РўРµРїРµСЂСЊ РІС‹ РјРѕР¶РµС‚Рµ СЃРЅРѕРІР° РїРѕР»СЊР·РѕРІР°С‚СЊСЃСЏ VPN.\n\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n'
        '<b>Р РµРєРѕРјРµРЅРґР°С†РёРё:</b>\n'
        'вЂў РЎР»РµРґРёС‚Рµ Р·Р° РєРѕР»РёС‡РµСЃС‚РІРѕРј СѓСЃС‚СЂРѕР№СЃС‚РІ\n'
        'вЂў РћС‚РєР»СЋС‡Р°Р№С‚Рµ VPN РєРѕРіРґР° РЅРµ РёСЃРїРѕР»СЊР·СѓРµС‚Рµ\n'
        'вЂў РќРµ РїСЂРµРІС‹С€Р°Р№С‚Рµ Р»РёРјРёС‚ РїРѕ С‚Р°СЂРёС„Сѓ'
    )

    # РЎРѕРѕР±С‰РµРЅРёРµ Рѕ Р±Р»РѕРєРёСЂРѕРІРєРµ Р·Р° WiFi
    # РџРµСЂРµРјРµРЅРЅС‹Рµ: {ban_minutes}, {network_info}, {node_info}
    BAN_MSG_WIFI: str = (
        '<b>РђРљРљРђРЈРќРў Р—РђР‘Р›РћРљРР РћР’РђРќ</b>\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n\n'
        '<b>РџСЂРёС‡РёРЅР°:</b> РСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ WiFi СЃРµС‚Рё\n'
        '{node_info}\n'
        '<b>Р”РµС‚Р°Р»Рё:</b>\n'
        'в”њ  РўРёРї РїРѕРґРєР»СЋС‡РµРЅРёСЏ: <b>WiFi</b>\n'
        '{network_info}'
        'в””  Р’СЂРµРјСЏ Р±Р»РѕРєРёСЂРѕРІРєРё: <b>{ban_minutes} РјРёРЅ</b>\n\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n'
        '<b>Р§С‚Рѕ РґРµР»Р°С‚СЊ:</b>\n'
        '1. РћС‚РєР»СЋС‡РёС‚РµСЃСЊ РѕС‚ WiFi\n'
        '2. РСЃРїРѕР»СЊР·СѓР№С‚Рµ РјРѕР±РёР»СЊРЅС‹Р№ РёРЅС‚РµСЂРЅРµС‚\n'
        '3. Р”РѕР¶РґРёС‚РµСЃСЊ РѕРєРѕРЅС‡Р°РЅРёСЏ Р±Р»РѕРєРёСЂРѕРІРєРё\n\n'
        'Р”РѕСЃС‚СѓРї РІРѕСЃСЃС‚Р°РЅРѕРІРёС‚СЃСЏ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё'
    )

    # РЎРѕРѕР±С‰РµРЅРёРµ Рѕ Р±Р»РѕРєРёСЂРѕРІРєРµ Р·Р° РјРѕР±РёР»СЊРЅСѓСЋ СЃРµС‚СЊ
    # РџРµСЂРµРјРµРЅРЅС‹Рµ: {ban_minutes}, {network_info}, {node_info}
    BAN_MSG_MOBILE: str = (
        '<b>РђРљРљРђРЈРќРў Р—РђР‘Р›РћРљРР РћР’РђРќ</b>\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n\n'
        '<b>РџСЂРёС‡РёРЅР°:</b> РСЃРїРѕР»СЊР·РѕРІР°РЅРёРµ РјРѕР±РёР»СЊРЅРѕР№ СЃРµС‚Рё\n'
        '{node_info}\n'
        '<b>Р”РµС‚Р°Р»Рё:</b>\n'
        'в”њ  РўРёРї РїРѕРґРєР»СЋС‡РµРЅРёСЏ: <b>РњРѕР±РёР»СЊРЅР°СЏ СЃРµС‚СЊ</b>\n'
        '{network_info}'
        'в””  Р’СЂРµРјСЏ Р±Р»РѕРєРёСЂРѕРІРєРё: <b>{ban_minutes} РјРёРЅ</b>\n\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n'
        '<b>Р§С‚Рѕ РґРµР»Р°С‚СЊ:</b>\n'
        '1. РџРѕРґРєР»СЋС‡РёС‚РµСЃСЊ Рє WiFi\n'
        '2. Р”РѕР¶РґРёС‚РµСЃСЊ РѕРєРѕРЅС‡Р°РЅРёСЏ Р±Р»РѕРєРёСЂРѕРІРєРё\n'
        '3. РСЃРїРѕР»СЊР·СѓР№С‚Рµ VPN С‚РѕР»СЊРєРѕ С‡РµСЂРµР· WiFi\n\n'
        'Р”РѕСЃС‚СѓРї РІРѕСЃСЃС‚Р°РЅРѕРІРёС‚СЃСЏ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё'
    )

    # РЎРѕРѕР±С‰РµРЅРёСЏ Рѕ С‚РёРїРёР·РёСЂРѕРІР°РЅРЅС‹С… СЂСѓС‡РЅС‹С… Р±Р°РЅР°С… BanHammer.
    # РџРµСЂРµРјРµРЅРЅС‹Рµ: {ban_minutes}, {reason}, {node_info}
    BAN_MSG_TORRENT: str = (
        'рџљ« <b>РђРљРљРђРЈРќРў Р—РђР‘Р›РћРљРР РћР’РђРќ</b>\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n\n'
        'вќЊ <b>РџСЂРёС‡РёРЅР°:</b> РћР±РЅР°СЂСѓР¶РµРЅР° torrent-Р°РєС‚РёРІРЅРѕСЃС‚СЊ\n'
        '{node_info}\n'
        'рџ“ќ <b>Р”РµС‚Р°Р»Рё:</b> {reason}\n'
        'вЏ± <b>Р’СЂРµРјСЏ Р±Р»РѕРєРёСЂРѕРІРєРё:</b> {ban_minutes} РјРёРЅ\n\n'
        'рџ”„ Р”РѕСЃС‚СѓРї РІРѕСЃСЃС‚Р°РЅРѕРІРёС‚СЃСЏ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РїРѕСЃР»Рµ РѕРєРѕРЅС‡Р°РЅРёСЏ Р±Р»РѕРєРёСЂРѕРІРєРё.'
    )
    BAN_MSG_HWID_LIMIT: str = (
        'рџљ« <b>РђРљРљРђРЈРќРў Р—РђР‘Р›РћРљРР РћР’РђРќ</b>\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n\n'
        'вќЊ <b>РџСЂРёС‡РёРЅР°:</b> РџСЂРµРІС‹С€РµРЅ Р»РёРјРёС‚ СѓСЃС‚СЂРѕР№СЃС‚РІ\n'
        '{node_info}\n'
        'рџ“ќ <b>Р”РµС‚Р°Р»Рё:</b> {reason}\n'
        'вЏ± <b>Р’СЂРµРјСЏ Р±Р»РѕРєРёСЂРѕРІРєРё:</b> {ban_minutes} РјРёРЅ\n\n'
        'рџ”„ Р”РѕСЃС‚СѓРї РІРѕСЃСЃС‚Р°РЅРѕРІРёС‚СЃСЏ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РїРѕСЃР»Рµ РѕРєРѕРЅС‡Р°РЅРёСЏ Р±Р»РѕРєРёСЂРѕРІРєРё.'
    )
    BAN_MSG_SUSPICIOUS_DESTINATION: str = (
        'рџљ« <b>РђРљРљРђРЈРќРў Р—РђР‘Р›РћРљРР РћР’РђРќ</b>\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n\n'
        'вќЊ <b>РџСЂРёС‡РёРЅР°:</b> РџРѕРґРєР»СЋС‡РµРЅРёРµ Рє Р·Р°РїСЂРµС‰С‘РЅРЅРѕРјСѓ СЂРµСЃСѓСЂСЃСѓ\n'
        '{node_info}\n'
        'рџ“ќ <b>Р”РµС‚Р°Р»Рё:</b> {reason}\n'
        'вЏ± <b>Р’СЂРµРјСЏ Р±Р»РѕРєРёСЂРѕРІРєРё:</b> {ban_minutes} РјРёРЅ\n\n'
        'рџ”„ Р”РѕСЃС‚СѓРї РІРѕСЃСЃС‚Р°РЅРѕРІРёС‚СЃСЏ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РїРѕСЃР»Рµ РѕРєРѕРЅС‡Р°РЅРёСЏ Р±Р»РѕРєРёСЂРѕРІРєРё.'
    )
    BAN_MSG_TRAFFIC_LIMIT: str = (
        'рџљ« <b>РђРљРљРђРЈРќРў Р—РђР‘Р›РћРљРР РћР’РђРќ</b>\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n\n'
        'вќЊ <b>РџСЂРёС‡РёРЅР°:</b> РџСЂРµРІС‹С€РµРЅ РґРѕРїСѓСЃС‚РёРјС‹Р№ РѕР±СЉС‘Рј С‚СЂР°С„РёРєР°\n'
        '{node_info}\n'
        'рџ“ќ <b>Р”РµС‚Р°Р»Рё:</b> {reason}\n'
        'вЏ± <b>Р’СЂРµРјСЏ Р±Р»РѕРєРёСЂРѕРІРєРё:</b> {ban_minutes} РјРёРЅ\n\n'
        'рџ”„ Р”РѕСЃС‚СѓРї РІРѕСЃСЃС‚Р°РЅРѕРІРёС‚СЃСЏ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РїРѕСЃР»Рµ РѕРєРѕРЅС‡Р°РЅРёСЏ Р±Р»РѕРєРёСЂРѕРІРєРё.'
    )
    BAN_MSG_MANUAL: str = (
        'рџљ« <b>РђРљРљРђРЈРќРў Р—РђР‘Р›РћРљРР РћР’РђРќ</b>\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n\n'
        'вќЊ <b>РџСЂРёС‡РёРЅР°:</b> РќР°СЂСѓС€РµРЅРёРµ РїСЂР°РІРёР» СЃРµСЂРІРёСЃР°\n'
        '{node_info}\n'
        'рџ“ќ <b>Р”РµС‚Р°Р»Рё:</b> {reason}\n'
        'вЏ± <b>Р’СЂРµРјСЏ Р±Р»РѕРєРёСЂРѕРІРєРё:</b> {ban_minutes} РјРёРЅ\n\n'
        'рџ”„ Р”РѕСЃС‚СѓРї РІРѕСЃСЃС‚Р°РЅРѕРІРёС‚СЃСЏ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё РїРѕСЃР»Рµ РѕРєРѕРЅС‡Р°РЅРёСЏ Р±Р»РѕРєРёСЂРѕРІРєРё.'
    )

    # РЎРѕРѕР±С‰РµРЅРёРµ-РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ
    # РџРµСЂРµРјРµРЅРЅС‹Рµ: {warning_message}
    BAN_MSG_WARNING: str = (
        '<b>РџР Р•Р”РЈРџР Р•Р–Р”Р•РќРР•</b>\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n\n'
        '{warning_message}\n\n'
        'в”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓв”Ѓ\n'
        'РџСЂРё РїРѕРІС‚РѕСЂРЅРѕРј РЅР°СЂСѓС€РµРЅРёРё Р°РєРєР°СѓРЅС‚ Р±СѓРґРµС‚ Р·Р°Р±Р»РѕРєРёСЂРѕРІР°РЅ'
    )

    DEBUG: bool = False
    WEBHOOK_URL: str | None = None
    WEBHOOK_PATH: str = '/webhook'
    WEBHOOK_SECRET_TOKEN: str | None = None
    WEBHOOK_IP: str | None = None  # IP Р°РґСЂРµСЃ РґР»СЏ setWebhook, С‡С‚РѕР±С‹ Telegram РЅРµ СЂРµР·РѕР»РІРёР» РґРѕРјРµРЅ
    WEBHOOK_DROP_PENDING_UPDATES: bool = True
    WEBHOOK_MAX_QUEUE_SIZE: int = 1024
    WEBHOOK_WORKERS: int = 4
    WEBHOOK_ENQUEUE_TIMEOUT: float = 0.1
    WEBHOOK_WORKER_SHUTDOWN_TIMEOUT: float = 30.0
    BOT_RUN_MODE: str = 'polling'

    WEB_API_ENABLED: bool = False
    WEB_API_HOST: str = '0.0.0.0'
    WEB_API_PORT: int = 8080
    WEB_API_WORKERS: int = 1
    WEB_API_ALLOWED_ORIGINS: str = '*'
    WEB_API_DOCS_ENABLED: bool = False
    WEB_API_TITLE: str = 'Remnawave Bot Admin API'
    WEB_API_VERSION: str = '1.0.0'
    WEB_API_DEFAULT_TOKEN: str | None = None
    WEB_API_DEFAULT_TOKEN_NAME: str = 'Bootstrap Token'
    WEB_API_TOKEN_HASH_ALGORITHM: str = 'sha256'
    WEB_API_TOKEN_HMAC_SECRET: str | None = None
    WEB_API_REQUEST_LOGGING: bool = True
    # РџРѕС‚РѕР»РѕРє РћР”РќРћР™ РѕРїРµСЂР°С†РёРё СЂСѓС‡РЅРѕРіРѕ РїРѕРїРѕР»РЅРµРЅРёСЏ С‡РµСЂРµР· POST /users/{id}/deposit.
    # Р­РЅРґРїРѕРёРЅС‚ СЂР°СЃСЃС‡РёС‚Р°РЅ РЅР° Р°РІС‚РѕРјР°С‚РёР·Р°С†РёСЋ (AI-Р°РіРµРЅС‚ РїРѕРґРґРµСЂР¶РєРё), РїРѕСЌС‚РѕРјСѓ Сѓ РЅРµРіРѕ РµСЃС‚СЊ
    # РїСЂРµРґРѕС…СЂР°РЅРёС‚РµР»СЊ: Р°РіРµРЅС‚, РѕС€РёР±С€РёР№СЃСЏ РЅР° РґРІР° РЅСѓР»СЏ, СѓРїСЂС‘С‚СЃСЏ РІ Р»РёРјРёС‚, Р° РЅРµ РїРѕРґР°СЂРёС‚
    # С‡РµР»РѕРІРµРєСѓ РіРѕРґРѕРІСѓСЋ РїРѕРґРїРёСЃРєСѓ. 0 вЂ” Р±РµР· РѕРіСЂР°РЅРёС‡РµРЅРёСЏ.
    WEB_API_MANUAL_DEPOSIT_MAX_KOPEKS: int = 1_000_000

    ENABLE_DEEP_LINKS: bool = True
    APP_CONFIG_CACHE_TTL: int = 3600

    VERSION_CHECK_ENABLED: bool = True
    VERSION_CHECK_REPO: str = 'fr1ngg/remnawave-@xilarobot-telegram-bot'
    VERSION_CHECK_INTERVAL_HOURS: int = 1

    BACKUP_AUTO_ENABLED: bool = True
    BACKUP_INTERVAL_HOURS: int = 24
    BACKUP_TIME: str = '03:00'
    BACKUP_MAX_KEEP: int = 7
    BACKUP_COMPRESSION: bool = True
    BACKUP_INCLUDE_LOGS: bool = False
    BACKUP_LOCATION: str = '/app/data/backups'
    BACKUP_SEND_ENABLED: bool = False
    BACKUP_SEND_CHAT_ID: str | None = None
    BACKUP_SEND_TOPIC_ID: int | None = None
    BACKUP_ARCHIVE_PASSWORD: str | None = None

    # Cabinet (Personal Account) settings
    CABINET_ENABLED: bool = False
    CABINET_JWT_SECRET: str | None = None
    CABINET_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    CABINET_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    CABINET_ALLOWED_ORIGINS: str = ''
    CABINET_EMAIL_VERIFICATION_ENABLED: bool = True
    CABINET_EMAIL_VERIFICATION_EXPIRE_HOURS: int = 24
    CABINET_PASSWORD_RESET_EXPIRE_HOURS: int = 1
    CABINET_EMAIL_CHANGE_CODE_EXPIRE_MINUTES: int = 15  # Email change verification code expiration
    CABINET_EMAIL_AUTH_ENABLED: bool = True  # Enable email registration/login in cabinet
    CABINET_URL: str = 'https://example.com/cabinet'  # Base URL for cabinet (used in verification emails)
    CABINET_TRUSTED_PROXIES: str = (
        ''  # Comma-separated IPs/CIDRs of trusted reverse proxies (e.g. '127.0.0.1,10.0.0.0/8')
    )

    # OAuth 2.0 provider settings for cabinet
    OAUTH_GOOGLE_CLIENT_ID: str = ''
    OAUTH_GOOGLE_CLIENT_SECRET: str = ''
    OAUTH_GOOGLE_ENABLED: bool = False

    OAUTH_YANDEX_CLIENT_ID: str = ''
    OAUTH_YANDEX_CLIENT_SECRET: str = ''
    OAUTH_YANDEX_ENABLED: bool = False

    OAUTH_DISCORD_CLIENT_ID: str = ''
    OAUTH_DISCORD_CLIENT_SECRET: str = ''
    OAUTH_DISCORD_ENABLED: bool = False

    OAUTH_VK_CLIENT_ID: str = ''
    OAUTH_VK_CLIENT_SECRET: str = ''
    OAUTH_VK_ENABLED: bool = False

    # SMTP settings for cabinet email
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_EMAIL: str | None = None
    SMTP_FROM_NAME: str = 'VPN Service'
    # РљСѓРґР° РґРѕР»Р¶РЅС‹ РїР°РґР°С‚СЊ РѕС‚РІРµС‚С‹ РєР»РёРµРЅС‚РѕРІ. РћС‚РїСЂР°РІРёС‚РµР»СЊ С‡Р°СЃС‚Рѕ Р¶РёРІС‘С‚ РЅР° РїРѕРґРґРѕРјРµРЅРµ
    # Р±РµР· MX (noreply@mail.example.com Сѓ Resend/SES) вЂ” РѕС‚РІРµС‚ РЅР° С‚Р°РєРѕРµ РїРёСЃСЊРјРѕ
    # РѕС‚Р±РёРІР°РµС‚СЃСЏ, Рё С‡РµР»РѕРІРµРє, РЅР°Р¶Р°РІС€РёР№ В«РћС‚РІРµС‚РёС‚СЊВ», СѓС…РѕРґРёС‚ РІ РЅРёРєСѓРґР°.
    SMTP_REPLY_TO: str = ''
    SMTP_USE_TLS: bool = True
    # Implicit TLS (SMTPS) вЂ” required for port 465. Auto-enabled when SMTP_PORT == 465.
    SMTP_USE_SSL: bool = False

    # РћС‚РїРёСЃРєР° РѕС‚ РјР°СЂРєРµС‚РёРЅРіРѕРІС‹С… РїРёСЃРµРј (winback, РїСЂРѕРјРѕРїСЂРµРґР»РѕР¶РµРЅРёСЏ, email-СЂР°СЃСЃС‹Р»РєРё).
    # Gmail/Yahoo РґР»СЏ bulk-РѕС‚РїСЂР°РІРёС‚РµР»РµР№ С‚СЂРµР±СѓСЋС‚ one-click unsubscribe (RFC 8058),
    # Р° Р¶Р°Р»РѕР±С‹ В«РЎРїР°РјВ» РІРјРµСЃС‚Рѕ РѕС‚РїРёСЃРєРё Р±СЊСЋС‚ РїРѕ СЂРµРїСѓС‚Р°С†РёРё РґРѕРјРµРЅР°.
    EMAIL_UNSUBSCRIBE_ENABLED: bool = True
    # РџСѓР±Р»РёС‡РЅС‹Р№ URL СЌРЅРґРїРѕРёРЅС‚Р° РѕС‚РїРёСЃРєРё. РџСѓСЃС‚Рѕ в†’ CABINET_URL + /api/cabinet/public/unsubscribe.
    # Р—Р°РґР°РІР°С‚СЊ СЏРІРЅРѕ, РµСЃР»Рё API РєР°Р±РёРЅРµС‚Р° РїСЂРѕРєСЃРёСЂСѓРµС‚СЃСЏ РЅРµ С‡РµСЂРµР· /api.
    EMAIL_UNSUBSCRIBE_BASE_URL: str = ''
    # РќРµРѕР±СЏР·Р°С‚РµР»СЊРЅС‹Р№ mailto-РІР°СЂРёР°РЅС‚ РІ List-Unsubscribe РґР»СЏ РєР»РёРµРЅС‚РѕРІ Р±РµР· HTTP one-click.
    EMAIL_UNSUBSCRIBE_MAILTO: str = ''

    # Ban System Integration (BedolagaBan monitoring)
    BAN_SYSTEM_ENABLED: bool = False
    BAN_SYSTEM_API_URL: str | None = None  # e.g., http://ban-server:8000
    BAN_SYSTEM_API_TOKEN: str | None = None
    BAN_SYSTEM_REQUEST_TIMEOUT: int = 30

    # SOCKS5 proxy for routing bot traffic to Telegram API
    # Format: socks5://user:password@host:port or socks5://host:port
    PROXY_URL: str | None = None

    # Custom Telegram Bot API server URL (for regions where api.telegram.org is blocked)
    # Examples: Cloudflare Worker proxy, self-hosted telegram-bot-api (tdlib), nginx reverse proxy
    TELEGRAM_API_URL: str | None = None

    @field_validator('PROXY_URL', 'NALOGO_PROXY_URL', mode='before')
    @classmethod
    def validate_proxy_url(cls, value: str | None) -> str | None:
        if not value:
            return None
        from urllib.parse import urlparse

        parsed = urlparse(value)
        if parsed.scheme not in ('socks5', 'socks5h', 'socks4'):
            raise ValueError(
                f'Proxy URL must use socks5://, socks5h://, or socks4:// scheme, got: {parsed.scheme!r}. '
                'HTTP proxies are not supported for security reasons.'
            )
        if not parsed.hostname:
            raise ValueError('Proxy URL must contain a hostname')
        return value

    @field_validator('MAIN_MENU_MODE', mode='before')
    @classmethod
    def normalize_main_menu_mode(cls, value: str | None) -> str:
        if not value:
            return 'default'

        normalized = str(value).strip().lower()
        aliases = {
            'classic': 'default',
            'default': 'default',
            'full': 'default',
            'standard': 'default',
            'cabinet': 'cabinet',
            'text': 'cabinet',
            'text_only': 'cabinet',
            'textual': 'cabinet',
            'minimal': 'cabinet',
        }

        mode = aliases.get(normalized, normalized)
        if mode not in {'default', 'cabinet'}:
            raise ValueError('MAIN_MENU_MODE must be one of: default, cabinet')
        return mode

    @field_validator('SERVER_STATUS_MODE', mode='before')
    @classmethod
    def normalize_server_status_mode(cls, value: str | None) -> str:
        if not value:
            return 'disabled'

        normalized = str(value).strip().lower()
        aliases = {
            'off': 'disabled',
            'none': 'disabled',
            'disabled': 'disabled',
            'external': 'external_link',
            'link': 'external_link',
            'url': 'external_link',
            'external_link': 'external_link',
            'miniapp': 'external_link_miniapp',
            'mini_app': 'external_link_miniapp',
            'mini-app': 'external_link_miniapp',
            'webapp': 'external_link_miniapp',
            'web_app': 'external_link_miniapp',
            'web-app': 'external_link_miniapp',
            'external_link_miniapp': 'external_link_miniapp',
            'xray': 'xray',
            'xraychecker': 'xray',
            'xray_metrics': 'xray',
            'metrics': 'xray',
        }

        mode = aliases.get(normalized, normalized)
        if mode not in {'disabled', 'external_link', 'external_link_miniapp', 'xray'}:
            raise ValueError('SERVER_STATUS_MODE must be one of: disabled, external_link, external_link_miniapp, xray')
        return mode

    @field_validator('GRACE_ACCESS_MODE', mode='before')
    @classmethod
    def normalize_grace_access_mode(cls, value: str | None) -> str:
        normalized = str(value or 'false').strip().lower()
        if normalized not in {'false', 'observe', 'true', 'drain'}:
            raise ValueError('GRACE_ACCESS_MODE must be one of: false, observe, true, drain')
        return normalized

    @field_validator(
        'GRACE_ACCESS_DURATION_HOURS',
        'GRACE_ACCESS_RECONCILE_INTERVAL_SECONDS',
        'GRACE_ACCESS_RECONCILE_BATCH_SIZE',
        'GRACE_ACCESS_CANDIDATE_LOOKBACK_MINUTES',
        mode='before',
    )
    @classmethod
    def ensure_positive_grace_access_value(cls, value: int | str) -> int:
        parsed = int(value)
        if parsed < 1:
            raise ValueError('Grace access duration, intervals, batch size and lookback must be positive')
        return parsed

    @field_validator('GRACE_ACCESS_TRAFFIC_GB', mode='before')
    @classmethod
    def ensure_nonnegative_grace_access_traffic(cls, value: int | str) -> int:
        parsed = int(value)
        if parsed < 0:
            raise ValueError('Grace access traffic must not be negative')
        return parsed

    @field_validator('SERVER_STATUS_ITEMS_PER_PAGE', mode='before')
    @classmethod
    def ensure_positive_server_status_page_size(cls, value: int | None) -> int:
        try:
            if value is None:
                return 10
            value_int = int(value)
            return max(1, value_int)
        except (TypeError, ValueError):
            return 10

    @field_validator('SERVER_STATUS_REQUEST_TIMEOUT', mode='before')
    @classmethod
    def ensure_positive_server_status_timeout(cls, value: int | None) -> int:
        try:
            if value is None:
                return 10
            value_int = int(value)
            return max(1, value_int)
        except (TypeError, ValueError):
            return 10

    @field_validator('DATABASE_POOL_SIZE', mode='before')
    @classmethod
    def ensure_positive_database_pool_size(cls, value: int | None) -> int:
        # pool_size=0 РІ SQLAlchemy QueuePool РѕР·РЅР°С‡Р°РµС‚ В«Р±РµР· Р»РёРјРёС‚Р°В» вЂ” СЌС‚Рѕ footgun,
        # РєРѕС‚РѕСЂС‹Р№ Р»РµРіРєРѕ РёСЃС‡РµСЂРїР°РµС‚ max_connections PostgreSQL, РїРѕСЌС‚РѕРјСѓ РґРµСЂР¶РёРј >= 1.
        try:
            if value is None or value == '':
                return 20
            return max(1, int(value))
        except (TypeError, ValueError):
            return 20

    @field_validator('DATABASE_MAX_OVERFLOW', mode='before')
    @classmethod
    def ensure_nonnegative_database_max_overflow(cls, value: int | None) -> int:
        try:
            if value is None or value == '':
                return 20
            return max(0, int(value))
        except (TypeError, ValueError):
            return 20

    @field_validator('DATABASE_POOL_TIMEOUT', mode='before')
    @classmethod
    def ensure_positive_database_pool_timeout(cls, value: int | None) -> int:
        try:
            if value is None or value == '':
                return 30
            return max(1, int(value))
        except (TypeError, ValueError):
            return 30

    @field_validator('LOG_FILE', mode='before')
    @classmethod
    def ensure_log_dir(cls, v):
        log_path = Path(v)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return str(log_path)

    def get_database_url(self) -> str:
        if self.DATABASE_URL and self.DATABASE_URL.strip():
            return self.DATABASE_URL

        mode = self.DATABASE_MODE.lower()

        if mode == 'sqlite':
            return self._get_sqlite_url()
        if mode == 'postgresql':
            return self._get_postgresql_url()
        if mode == 'auto':
            if os.getenv('DOCKER_ENV') == 'true' or os.path.exists('/.dockerenv'):
                return self._get_postgresql_url()
            return self._get_sqlite_url()
        return self._get_auto_database_url()

    def _get_sqlite_url(self) -> str:
        sqlite_path = Path(self.SQLITE_PATH)
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        return f'sqlite+aiosqlite:///{sqlite_path.absolute()}'

    def _get_postgresql_url(self) -> str:
        return (
            f'postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}'
            f'@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}'
        )

    def _get_auto_database_url(self) -> str:
        if os.getenv('DOCKER_ENV') == 'true' or os.path.exists('/.dockerenv'):
            return self._get_postgresql_url()
        return self._get_sqlite_url()

    def is_postgresql(self) -> bool:
        """РџСЂРѕРІРµСЂСЏРµС‚, РёСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ Р»Рё PostgreSQL"""
        return 'postgresql' in self.get_database_url()

    def is_sqlite(self) -> bool:
        """РџСЂРѕРІРµСЂСЏРµС‚, РёСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ Р»Рё SQLite"""
        return 'sqlite' in self.get_database_url()

    def get_proxy_url(self) -> str | None:
        """Return SOCKS5 proxy URL or None."""
        return self.PROXY_URL or None

    def get_telegram_api_url(self) -> str | None:
        """Return custom Telegram Bot API server URL or None."""
        return self.TELEGRAM_API_URL or None

    def get_nalogo_proxy_url(self) -> str | None:
        """Return SOCKS proxy URL for nalogo or None.

        Uses NALOGO_PROXY_URL if set, otherwise falls back to PROXY_URL.
        """
        return self.NALOGO_PROXY_URL or self.PROXY_URL

    def is_admin(self, telegram_id: int | None = None, email: str | None = None) -> bool:
        """
        Check if user is admin by telegram_id or email.

        Args:
            telegram_id: Telegram user ID
            email: User email address

        Returns:
            True if user is admin
        """
        if telegram_id and telegram_id in self.get_admin_ids():
            return True
        if email and email.lower() in [e.lower() for e in self.get_admin_emails()]:
            return True
        return False

    def get_admin_ids(self) -> list[int]:
        try:
            admin_ids = self.ADMIN_IDS

            if isinstance(admin_ids, str):
                if not admin_ids.strip():
                    return []
                return [int(x.strip()) for x in admin_ids.split(',') if x.strip()]

            return []

        except (ValueError, AttributeError):
            return []

    def get_admin_emails(self) -> list[str]:
        """Get list of admin emails for email-only users."""
        try:
            admin_emails = self.ADMIN_EMAILS

            if isinstance(admin_emails, str):
                if not admin_emails.strip():
                    return []
                return [e.strip().lower() for e in admin_emails.split(',') if e.strip()]

            return []

        except (ValueError, AttributeError):
            return []

    def get_test_email(self) -> str | None:
        """Get test email for development/testing."""
        email = (self.TEST_EMAIL or '').strip().lower()
        return email or None

    def get_test_email_password(self) -> str | None:
        """Get test email password."""
        password = (self.TEST_EMAIL_PASSWORD or '').strip()
        return password or None

    def is_test_email(self, email: str) -> bool:
        """Check if email is the configured test email."""
        test_email = self.get_test_email()
        if not test_email:
            return False
        return email.lower().strip() == test_email

    def validate_test_email_password(self, email: str, password: str) -> bool:
        """Validate test email credentials."""
        if not self.is_test_email(email):
            return False
        test_password = self.get_test_email_password()
        if not test_password:
            return False
        return password == test_password

    def get_remnawave_auth_params(self) -> dict[str, str | None]:
        return {
            'base_url': self.REMNAWAVE_API_URL,
            'api_key': self.REMNAWAVE_API_KEY,
            'secret_key': self.REMNAWAVE_SECRET_KEY,
            'username': self.REMNAWAVE_USERNAME,
            'password': self.REMNAWAVE_PASSWORD,
            'caddy_token': self.REMNAWAVE_CADDY_TOKEN,
            'auth_type': self.REMNAWAVE_AUTH_TYPE,
        }

    def get_pal24_sbp_button_text(self, fallback: str) -> str:
        value = (self.PAL24_SBP_BUTTON_TEXT or '').strip()
        return value or fallback

    def get_pal24_card_button_text(self, fallback: str) -> str:
        value = (self.PAL24_CARD_BUTTON_TEXT or '').strip()
        return value or fallback

    def is_pal24_sbp_button_visible(self) -> bool:
        return self.PAL24_SBP_BUTTON_VISIBLE

    def is_pal24_card_button_visible(self) -> bool:
        return self.PAL24_CARD_BUTTON_VISIBLE

    def get_remnawave_user_delete_mode(self) -> str:
        """Р’РѕР·РІСЂР°С‰Р°РµС‚ СЂРµР¶РёРј СѓРґР°Р»РµРЅРёСЏ РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№: 'delete' РёР»Рё 'disable'"""
        mode = self.REMNAWAVE_USER_DELETE_MODE.lower().strip()
        return mode if mode in ['delete', 'disable'] else 'delete'

    def format_remnawave_user_description(
        self,
        *,
        full_name: str,
        username: str | None,
        telegram_id: int | None,
        email: str | None = None,
        user_id: int | None = None,
    ) -> str:
        """
        Р¤РѕСЂРјР°С‚РёСЂСѓРµС‚ РѕРїРёСЃР°РЅРёРµ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ РґР»СЏ RemnaWave.

        РџРѕРґРґРµСЂР¶РёРІР°РµС‚ РєР°Рє Telegram-РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№, С‚Р°Рє Рё email-only РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№.
        """
        template = self.REMNAWAVE_USER_DESCRIPTION_TEMPLATE or 'Bot user: {full_name} {username}'
        template_for_formatting = template.replace('@{username}', '{username}')

        username_clean = (username or '').lstrip('@')

        # Р¤РѕСЂРјРёСЂСѓРµРј РёРґРµРЅС‚РёС„РёРєР°С‚РѕСЂ РґР»СЏ description
        identifier_parts = []
        if telegram_id:
            identifier_parts.append(f'TG: {telegram_id}')
        if email:
            identifier_parts.append(f'Email: {email}')
        if user_id and not identifier_parts:
            identifier_parts.append(f'ID: {user_id}')

        values = defaultdict(
            str,
            {
                'full_name': full_name,
                'username': f'@{username_clean}' if username_clean else '',
                'username_clean': username_clean,
                'telegram_id': str(telegram_id) if telegram_id else '',
                'email': email or '',
                'user_id': str(user_id) if user_id else '',
                'identifier': ' | '.join(identifier_parts),
            },
        )

        description = template_for_formatting.format_map(values)

        if not username_clean:
            description = re.sub(r'@(?=\W|$)', '', description)
            description = re.sub(r'\(\s*\)', '', description)

        description = re.sub(r'\s+', ' ', description).strip()
        return description

    # RemnaWave API enforces `username` length: 3..36 chars inclusive.
    # ClassVar вЂ” СЌС‚Рѕ РєРѕРЅСЃС‚Р°РЅС‚С‹ РєРѕРґР°, Р° РЅРµ env-tunable РїРѕР»СЏ Settings.
    REMNAWAVE_USERNAME_MAX_LENGTH: ClassVar[int] = 36
    REMNAWAVE_USERNAME_MIN_LENGTH: ClassVar[int] = 3

    def format_remnawave_username(
        self,
        *,
        full_name: str,
        username: str | None,
        telegram_id: int | None,
        email: str | None = None,
        user_id: int | None = None,
        reserve_suffix_chars: int = 0,
    ) -> str:
        """
        Р¤РѕСЂРјР°С‚РёСЂСѓРµС‚ username РґР»СЏ RemnaWave.

        Р”Р»СЏ email-РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№ (telegram_id=None) РёСЃРїРѕР»СЊР·СѓРµС‚ email prefix + user_id.

        ``reserve_suffix_chars`` СЂРµР·РµСЂРІРёСЂСѓРµС‚ РјРµСЃС‚Рѕ РґР»СЏ СЃСѓС„С„РёРєСЃР°, РєРѕС‚РѕСЂС‹Р№ caller
        СЃРѕР±РёСЂР°РµС‚СЃСЏ РїСЂРёРєР»РµРёС‚СЊ (РЅР°РїСЂРёРјРµСЂ, `_<remnawave_short_id>`). Truncate
        РїСЂРѕРёСЃС…РѕРґРёС‚ Р”Рћ РєРѕРЅРєР°С‚РµРЅР°С†РёРё, С‡С‚РѕР±С‹ РёС‚РѕРіРѕРІР°СЏ СЃС‚СЂРѕРєР° С‚РѕС‡РЅРѕ РІР»РµР·Р°Р»Р° РІ
        REMNAWAVE_USERNAME_MAX_LENGTH. Р”РµС„РѕР»С‚ 0 вЂ” РѕР±СЂР°С‚РЅР°СЏ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚СЊ.
        """
        template = self.REMNAWAVE_USER_USERNAME_TEMPLATE or 'user_{telegram_id}'

        username_clean = (username or '').lstrip('@')
        full_name_value = full_name or ''

        # Remnawave СЂР°Р·СЂРµС€Р°РµС‚ С‚РѕР»СЊРєРѕ Р±СѓРєРІС‹, С†РёС„СЂС‹, РїРѕРґС‡С‘СЂРєРёРІР°РЅРёСЏ Рё РґРµС„РёСЃС‹
        def _sanitize(value: str) -> str:
            result = re.sub(r'[^0-9A-Za-z_-]+', '_', value)
            return re.sub(r'_+', '_', result).strip('_-')

        # Р”Р»СЏ email-РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№ С„РѕСЂРјРёСЂСѓРµРј СѓРЅРёРєР°Р»СЊРЅС‹Р№ identifier
        if telegram_id:
            identifier = str(telegram_id)
        elif email:
            email_prefix = _sanitize(email.split('@')[0][:10])
            identifier = _sanitize(f'email_{email_prefix}_{user_id}' if user_id else f'email_{email_prefix}')
        elif user_id:
            identifier = f'id_{user_id}'
        else:
            identifier = 'unknown'

        # NB: РґР»СЏ email-only users СЃР»РѕС‚ {telegram_id} Р·Р°РїРѕР»РЅСЏРµС‚СЃСЏ identifier'РѕРј
        # (legacy fallback РґР»СЏ С€Р°Р±Р»РѕРЅРѕРІ, РЅРµ РёСЃРїРѕР»СЊР·СѓСЋС‰РёС… {identifier}). Р­С‚Рѕ
        # РјРѕР¶РµС‚ РїСЂРёРІРѕРґРёС‚СЊ Рє РґСѓР±Р»РёСЂРѕРІР°РЅРёСЋ email-РїСЂРµС„РёРєСЃР°, РµСЃР»Рё С€Р°Р±Р»РѕРЅ СЃСЃС‹Р»Р°РµС‚СЃСЏ
        # РѕРґРЅРѕРІСЂРµРјРµРЅРЅРѕ РЅР° {email} Р {telegram_id} вЂ” С„РёРЅР°Р»СЊРЅС‹Р№ length cap РЅРёР¶Рµ
        # РѕР±СЂРµР·Р°РµС‚ СЃС‚СЂРѕРєСѓ, РЅРѕ СЃРµРјР°РЅС‚РёС‡РµСЃРєР°СЏ РґСѓРїР»РёРєР°С†РёСЏ РѕСЃС‚Р°С‘С‚СЃСЏ. Р РµРєРѕРјРµРЅРґСѓРµРјС‹Р№
        # С€Р°Р±Р»РѕРЅ РґР»СЏ СЃРјРµС€Р°РЅРЅС‹С… РґРµРїР»РѕРµРІ: `{username_clean}_{identifier}`.
        values = defaultdict(
            str,
            {
                'full_name': full_name_value,
                'username': username_clean,
                'username_clean': username_clean,
                'telegram_id': str(telegram_id) if telegram_id else identifier,
                'identifier': identifier,
                'email': _sanitize(email.split('@')[0]) if email else '',
                'user_id': str(user_id) if user_id else '',
            },
        )

        raw_username = template.format_map(values).strip()
        sanitized_username = _sanitize(raw_username)

        # Degenerate render: РЅРё РѕРґРЅР° РїРµСЂРµРјРµРЅРЅР°СЏ С€Р°Р±Р»РѕРЅР° РЅРµ РґР°Р»Р° СѓРЅРёРєР°Р»СЊРЅРѕРіРѕ
        # Р·РЅР°С‡РµРЅРёСЏ. РќР°РїСЂ. С€Р°Р±Р»РѕРЅ `user_{username}` РґР»СЏ email-only СЋР·РµСЂР° (Сѓ
        # РєРѕС‚РѕСЂРѕРіРѕ РЅРµС‚ Telegram-username) СЂРµРЅРґРµСЂРёС‚СЃСЏ РІ `user` вЂ” РѕРґРёРЅР°РєРѕРІРѕ РґР»СЏ
        # Р’РЎР•РҐ С‚Р°РєРёС… СЋР·РµСЂРѕРІ в†’ RemnaWave РѕС‚РІРµС‡Р°РµС‚ 409 "username already exists"
        # РЅР° РєР°Р¶РґСѓСЋ СЂРµРіРёСЃС‚СЂР°С†РёСЋ РїРѕСЃР»Рµ РїРµСЂРІРѕР№. `skeleton` вЂ” С‚РѕС‚ Р¶Рµ С€Р°Р±Р»РѕРЅ СЃ
        # РїСѓСЃС‚С‹РјРё РїРµСЂРµРјРµРЅРЅС‹РјРё; СЂР°РІРµРЅСЃС‚РІРѕ РµРјСѓ Р·РЅР°С‡РёС‚ В«С€Р°Р±Р»РѕРЅ РЅРёС‡РµРіРѕ РЅРµ РґР°Р»В».
        skeleton = _sanitize(template.format_map(defaultdict(str)))
        if not sanitized_username or sanitized_username == skeleton:
            sanitized_username = _sanitize(f'user_{identifier}')

        # Р РµР·РµСЂРІРёСЂСѓРµРј РјРµСЃС‚Рѕ РїРѕРґ caller-suffix, РЅРµ РѕРїСѓСЃРєР°СЏСЃСЊ РЅРёР¶Рµ РјРёРЅРёРјР°Р»СЊРЅРѕР№ РґР»РёРЅС‹.
        max_len = max(
            self.REMNAWAVE_USERNAME_MIN_LENGTH,
            self.REMNAWAVE_USERNAME_MAX_LENGTH - max(0, reserve_suffix_chars),
        )
        result = sanitized_username[:max_len].strip('_-') or 'user'

        # RemnaWave С‚СЂРµР±СѓРµС‚ username РјРёРЅРёРјСѓРј 3 СЃРёРјРІРѕР»Р°
        if len(result) < self.REMNAWAVE_USERNAME_MIN_LENGTH:
            result = f'{result}_{identifier}'[:max_len].strip('_-')

        return result or 'user'

    def build_remnawave_subscription_username(
        self,
        *,
        full_name: str,
        username: str | None,
        telegram_id: int | None,
        email: str | None,
        user_id: int | None,
        suffix: str,
    ) -> str:
        """Build a RemnaWave username with a known suffix, guaranteed within the API limit.

        `suffix` is expected pre-formatted with its separator (e.g. '_49883b').
        Р РµР·РµСЂРІРёСЂСѓРµРј РјРµСЃС‚Рѕ РїРѕРґ suffix РІ base, РґРµР»Р°РµРј belt-and-suspenders С„РёРЅР°Р»СЊРЅРѕРµ
        РѕРіСЂР°РЅРёС‡РµРЅРёРµ РґР»РёРЅС‹. РСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ РІ multi-tariff create-paths, РіРґРµ Рє base
        РїСЂРёРєР»РµРёРІР°РµС‚СЃСЏ `_<remnawave_short_id>`.
        """
        base = self.format_remnawave_username(
            full_name=full_name,
            username=username,
            telegram_id=telegram_id,
            email=email,
            user_id=user_id,
            reserve_suffix_chars=len(suffix),
        )
        result = f'{base}{suffix}'
        if len(result) > self.REMNAWAVE_USERNAME_MAX_LENGTH:
            # Suffix РєСЂРёС‚РёС‡РµРЅ (СѓРЅРёРєР°Р»РµРЅ per-subscription) вЂ” СЂРµР¶РµРј base.
            # max(0, ...) Р·Р°С‰РёС‰Р°РµС‚ РѕС‚ СЃРёС‚СѓР°С†РёРё, РєРѕРіРґР° suffix СЃР°Рј РґР»РёРЅРЅРµРµ Р»РёРјРёС‚Р°:
            # Р±РµР· С„Р»РѕСЂР° base[:-N] РјРѕР»С‡Р° РІРѕР·РІСЂР°С‰Р°Р» Р±С‹ С…РІРѕСЃС‚ СЃС‚СЂРѕРєРё.
            keep_for_base = max(0, self.REMNAWAVE_USERNAME_MAX_LENGTH - len(suffix))
            result = f'{base[:keep_for_base].rstrip("_-")}{suffix}'
            # Final clamp РЅР° СЃР»СѓС‡Р°Р№, РєРѕРіРґР° suffix РІСЃС‘-С‚Р°РєРё РїСЂРµРІС‹С€Р°РµС‚ Р»РёРјРёС‚.
            result = result[: self.REMNAWAVE_USERNAME_MAX_LENGTH]
        return result

    @staticmethod
    def parse_daily_time_list(raw_value: str | None) -> list[time]:
        if not raw_value:
            return []

        segments = re.split(r'[\s,;]+', raw_value.strip())
        seen: set[tuple[int, int]] = set()
        parsed: list[time] = []

        for segment in segments:
            if not segment:
                continue

            try:
                hours_str, minutes_str = segment.split(':', 1)
                hours = int(hours_str)
                minutes = int(minutes_str)
            except (ValueError, AttributeError):
                continue

            if not (0 <= hours < 24 and 0 <= minutes < 60):
                continue

            key = (hours, minutes)
            if key in seen:
                continue

            seen.add(key)
            parsed.append(time(hour=hours, minute=minutes))

        parsed.sort()
        return parsed

    def get_remnawave_auto_sync_times(self) -> list[time]:
        return self.parse_daily_time_list(self.REMNAWAVE_AUTO_SYNC_TIMES)

    def is_remnawave_webhook_enabled(self) -> bool:
        return (
            self.REMNAWAVE_WEBHOOK_ENABLED
            and bool(self.REMNAWAVE_WEBHOOK_SECRET)
            and len(self.REMNAWAVE_WEBHOOK_SECRET or '') >= 32
        )

    def get_traffic_monitored_nodes(self) -> list[str]:
        """Р’РѕР·РІСЂР°С‰Р°РµС‚ СЃРїРёСЃРѕРє UUID РЅРѕРґ РґР»СЏ РјРѕРЅРёС‚РѕСЂРёРЅРіР° (РїСѓСЃС‚Рѕ = РІСЃРµ)"""
        if not self.TRAFFIC_MONITORED_NODES:
            return []
        # РЈР±РёСЂР°РµРј РєРѕРјРјРµРЅС‚Р°СЂРёРё (РІСЃРµ РїРѕСЃР»Рµ #)
        value = self.TRAFFIC_MONITORED_NODES.split('#')[0].strip()
        if not value:
            return []
        return [n.strip() for n in value.split(',') if n.strip()]

    def get_traffic_ignored_nodes(self) -> list[str]:
        """Р’РѕР·РІСЂР°С‰Р°РµС‚ СЃРїРёСЃРѕРє UUID РЅРѕРґ РґР»СЏ РёСЃРєР»СЋС‡РµРЅРёСЏ РёР· РјРѕРЅРёС‚РѕСЂРёРЅРіР°"""
        if not self.TRAFFIC_IGNORED_NODES:
            return []
        # РЈР±РёСЂР°РµРј РєРѕРјРјРµРЅС‚Р°СЂРёРё (РІСЃРµ РїРѕСЃР»Рµ #)
        value = self.TRAFFIC_IGNORED_NODES.split('#')[0].strip()
        if not value:
            return []
        return [n.strip() for n in value.split(',') if n.strip()]

    def get_traffic_excluded_user_uuids(self) -> list[str]:
        """Р’РѕР·РІСЂР°С‰Р°РµС‚ СЃРїРёСЃРѕРє UUID РїРѕР»СЊР·РѕРІР°С‚РµР»РµР№ РґР»СЏ РёСЃРєР»СЋС‡РµРЅРёСЏ РёР· РјРѕРЅРёС‚РѕСЂРёРЅРіР° (РЅР°РїСЂРёРјРµСЂ, С‚СѓРЅРµР»СЊРЅС‹Рµ/СЃР»СѓР¶РµР±РЅС‹Рµ)"""
        if not self.TRAFFIC_EXCLUDED_USER_UUIDS:
            return []
        # РЈР±РёСЂР°РµРј РєРѕРјРјРµРЅС‚Р°СЂРёРё (РІСЃРµ РїРѕСЃР»Рµ #)
        value = self.TRAFFIC_EXCLUDED_USER_UUIDS.split('#')[0].strip()
        if not value:
            return []
        return [uuid.strip().lower() for uuid in value.split(',') if uuid.strip()]

    def get_traffic_daily_check_time(self) -> time | None:
        """Р’РѕР·РІСЂР°С‰Р°РµС‚ РІСЂРµРјСЏ СЃСѓС‚РѕС‡РЅРѕР№ РїСЂРѕРІРµСЂРєРё С‚СЂР°С„РёРєР°"""
        times = self.parse_daily_time_list(self.TRAFFIC_DAILY_CHECK_TIME)
        return times[0] if times else None

    def get_display_name_banned_keywords(self) -> list[str]:
        raw_value = self.DISPLAY_NAME_BANNED_KEYWORDS
        if raw_value is None:
            return []

        if isinstance(raw_value, str):
            candidates = re.split(r'[\n,]+', raw_value)
        else:
            candidates = list(raw_value)

        unique: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            normalized = str(candidate).strip().lower()
            if not normalized:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            unique.append(normalized)

        return unique

    def get_autopay_warning_days(self) -> list[int]:
        try:
            days = self.AUTOPAY_WARNING_DAYS
            if isinstance(days, str):
                if not days.strip():
                    return [3, 1]
                return [int(x.strip()) for x in days.split(',') if x.strip()]
            return [3, 1]
        except (ValueError, AttributeError):
            return [3, 1]

    def is_autopay_enabled_by_default(self) -> bool:
        value = getattr(self, 'DEFAULT_AUTOPAY_ENABLED', True)

        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized in {'1', 'true', 'yes', 'on'}

        return bool(value)

    def is_auto_purchase_after_topup_enabled(self) -> bool:
        value = getattr(self, 'AUTO_PURCHASE_AFTER_TOPUP_ENABLED', False)

        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized in {'1', 'true', 'yes', 'on'}

        return bool(value)

    def get_available_languages(self) -> list[str]:
        defaults = ['ru', 'en', 'ua', 'zh']

        try:
            langs = self.AVAILABLE_LANGUAGES
        except AttributeError:
            return defaults

        candidates: list[str]

        if isinstance(langs, str):
            if not langs.strip():
                return defaults
            candidates = [chunk.strip() for chunk in langs.split(',')]
        elif isinstance(langs, (list, tuple, set)):
            candidates = [str(item).strip() for item in langs]
        else:
            return defaults

        cleaned: list[str] = []
        seen: set[str] = set()

        for code in candidates:
            if not code:
                continue

            normalized = code.lower()

            if normalized in seen:
                continue

            seen.add(normalized)
            cleaned.append(code)

        return cleaned or defaults

    def is_language_selection_enabled(self) -> bool:
        return bool(getattr(self, 'LANGUAGE_SELECTION_ENABLED', True))

    def format_price(self, price_kopeks: int, round_kopeks: bool | None = None) -> str:
        """
        Р¤РѕСЂРјР°С‚РёСЂСѓРµС‚ С†РµРЅСѓ РІ РєРѕРїРµР№РєР°С… РґР»СЏ РѕС‚РѕР±СЂР°Р¶РµРЅРёСЏ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ.

        Args:
            price_kopeks: РЎСѓРјРјР° РІ РєРѕРїРµР№РєР°С…
            round_kopeks: Р•СЃР»Рё True, РѕРєСЂСѓРіР»СЏРµС‚ РєРѕРїРµР№РєРё (в‰¤50 РІРЅРёР·, >50 РІРІРµСЂС…).
                         Р•СЃР»Рё None, РёСЃРїРѕР»СЊР·СѓРµС‚ РЅР°СЃС‚СЂРѕР№РєСѓ PRICE_ROUNDING_ENABLED.

        Returns:
            РћС‚С„РѕСЂРјР°С‚РёСЂРѕРІР°РЅРЅР°СЏ СЃС‚СЂРѕРєР° С†РµРЅС‹ (РЅР°РїСЂРёРјРµСЂ, "150 в‚Ѕ")
        """
        # РСЃРїРѕР»СЊР·СѓРµРј РЅР°СЃС‚СЂРѕР№РєСѓ РµСЃР»Рё РЅРµ РїРµСЂРµРґР°РЅРѕ СЏРІРЅРѕ
        should_round = round_kopeks if round_kopeks is not None else self.PRICE_ROUNDING_ENABLED

        sign = '-' if price_kopeks < 0 else ''
        abs_kopeks = abs(price_kopeks)
        rubles, kopeks = divmod(abs_kopeks, 100)

        if should_round:
            # РћРєСЂСѓРіР»РµРЅРёРµ: в‰¤50 РєРѕРї РІРЅРёР·, >50 РєРѕРї РІРІРµСЂС…
            if kopeks > 50:
                rubles += 1
            return f'{sign}{rubles} в‚Ѕ'

        # Р‘РµР· РѕРєСЂСѓРіР»РµРЅРёСЏ - РїРѕРєР°Р·С‹РІР°РµРј С‚РѕС‡РЅРѕРµ Р·РЅР°С‡РµРЅРёРµ
        if kopeks:
            value = f'{sign}{rubles}.{kopeks:02d}'.rstrip('0').rstrip('.')
            return f'{value} в‚Ѕ'

        return f'{sign}{rubles} в‚Ѕ'

    def get_reports_chat_id(self) -> str | None:
        if self.ADMIN_REPORTS_CHAT_ID:
            return self.ADMIN_REPORTS_CHAT_ID
        return self.ADMIN_NOTIFICATIONS_CHAT_ID

    def get_reports_topic_id(self) -> int | None:
        return self.ADMIN_REPORTS_TOPIC_ID or None

    def get_reports_send_time(self) -> time | None:
        value = self.ADMIN_REPORTS_SEND_TIME
        if not value:
            return None

        try:
            hours_str, minutes_str = value.strip().split(':', 1)
            hours = int(hours_str)
            minutes = int(minutes_str)
            if not (0 <= hours <= 23 and 0 <= minutes <= 59):
                raise ValueError
            return time(hour=hours, minute=minutes)
        except (ValueError, AttributeError):
            logger.warning('РќРµРєРѕСЂСЂРµРєС‚РЅРѕРµ Р·РЅР°С‡РµРЅРёРµ ADMIN_REPORTS_SEND_TIME', send_time_value=value)
            return None

    def kopeks_to_rubles(self, kopeks: int) -> float:
        return kopeks / 100

    def rubles_to_kopeks(self, rubles: float) -> int:
        return int(rubles * 100)

    @staticmethod
    def _normalize_user_tag(value: str | None, setting_name: str) -> str | None:
        if value is None:
            return None

        cleaned = str(value).strip().upper()
        if not cleaned:
            return None

        if len(cleaned) > 16:
            logger.warning(
                'РќРµРєРѕСЂСЂРµРєС‚РЅР°СЏ РґР»РёРЅР° : РјР°РєСЃРёРјСѓРј 16 СЃРёРјРІРѕР»РѕРІ, РїРѕР»СѓС‡РµРЅРѕ',
                setting_name=setting_name,
                cleaned_count=len(cleaned),
            )
            return None

        if not USER_TAG_PATTERN.fullmatch(cleaned):
            logger.warning(
                'РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ С„РѕСЂРјР°С‚ : РґРѕРїСѓСЃС‚РёРјС‹ С‚РѕР»СЊРєРѕ A-Z, 0-9 Рё РїРѕРґС‡С‘СЂРєРёРІР°РЅРёРµ',
                setting_name=setting_name,
            )
            return None

        return cleaned

    def get_trial_warning_hours(self) -> int:
        return self.TRIAL_WARNING_HOURS

    def get_trial_user_tag(self) -> str | None:
        return self._normalize_user_tag(self.TRIAL_USER_TAG, 'TRIAL_USER_TAG')

    def is_trial_disabled_for_user(self, auth_type: str | None) -> bool:
        disabled_for = self.TRIAL_DISABLED_FOR
        if disabled_for == 'all':
            return True
        # 'email' means all non-Telegram users (email, google, yandex, discord, vk, etc.)
        if disabled_for == 'email' and auth_type not in (None, 'telegram'):
            return True
        if disabled_for == 'telegram' and (auth_type is None or auth_type == 'telegram'):
            return True
        return False

    def get_paid_subscription_user_tag(self) -> str | None:
        return self._normalize_user_tag(
            self.PAID_SUBSCRIPTION_USER_TAG,
            'PAID_SUBSCRIPTION_USER_TAG',
        )

    def get_grace_user_tag(self) -> str | None:
        return self._normalize_user_tag(self.GRACE_USER_TAG, 'GRACE_USER_TAG')

    def get_bot_username(self) -> str | None:
        username = getattr(self, 'BOT_USERNAME', None)
        if not username:
            return None
        normalized = str(username).strip().lstrip('@')
        return normalized or None

    def is_notifications_enabled(self) -> bool:
        return self.ENABLE_NOTIFICATIONS

    def get_main_menu_mode(self) -> str:
        return getattr(self, 'MAIN_MENU_MODE', 'default')

    def is_cabinet_mode(self) -> bool:
        return self.get_main_menu_mode() == 'cabinet'

    def is_text_main_menu_mode(self) -> bool:
        """Backward-compatible alias for :meth:`is_cabinet_mode`."""
        return self.is_cabinet_mode()

    def get_main_menu_miniapp_url(self) -> str | None:
        for candidate in [self.MINIAPP_CUSTOM_URL, self.MINIAPP_PURCHASE_URL]:
            value = (candidate or '').strip()
            if value:
                return value
        return None

    _CABINET_URL_DEFAULT = 'https://example.com/cabinet'

    def _encode_referral_code(self, referral_code: str) -> str:
        """Validate and URL-encode a referral code."""
        if not referral_code:
            raise ValueError('referral_code must not be empty or None')
        return _url_quote(referral_code, safe='')

    def _normalized_cabinet_url(self) -> str | None:
        """Return normalized cabinet URL, or None if not configured."""
        cabinet_url = (self.CABINET_URL or '').strip().rstrip('/')
        if not cabinet_url or cabinet_url == self._CABINET_URL_DEFAULT:
            return None
        return cabinet_url

    def get_referral_link(self, referral_code: str, bot_username: str | None = None) -> str:
        """Build a referral link pointing to the web cabinet.

        Falls back to a Telegram bot deep link when CABINET_URL is not configured.
        """
        cabinet_link = self.get_cabinet_referral_link(referral_code)
        if cabinet_link:
            return cabinet_link
        return self.get_bot_referral_link(referral_code, bot_username)

    def get_bot_referral_link(self, referral_code: str, bot_username: str | None = None) -> str:
        """Always return the Telegram bot deep link for a referral code."""
        safe_code = self._encode_referral_code(referral_code)
        username = bot_username or self.get_bot_username() or 'bot'
        return f'https://t.me/{username}?start={safe_code}'

    def get_cabinet_referral_link(self, referral_code: str) -> str | None:
        """Return the cabinet referral link, or None if cabinet is not configured."""
        cabinet_url = self._normalized_cabinet_url()
        if not cabinet_url:
            return None
        safe_code = self._encode_referral_code(referral_code)
        sep = '&' if '?' in cabinet_url else '?'
        return f'{cabinet_url}{sep}ref={safe_code}'

    def is_deep_links_enabled(self) -> bool:
        return self.ENABLE_DEEP_LINKS

    def get_miniapp_branding(self) -> dict[str, dict[str, str | None]]:
        def _clean(value: str | None) -> str | None:
            if value is None:
                return None
            value_str = str(value).strip()
            return value_str or None

        name_en = _clean(self.MINIAPP_SERVICE_NAME_EN)
        name_ru = _clean(self.MINIAPP_SERVICE_NAME_RU)
        desc_en = _clean(self.MINIAPP_SERVICE_DESCRIPTION_EN)
        desc_ru = _clean(self.MINIAPP_SERVICE_DESCRIPTION_RU)

        default_name = name_en or name_ru or 'RemnaWave VPN'
        default_description = desc_en or desc_ru or 'Secure & Fast Connection'

        return {
            'service_name': {
                'default': default_name,
                'en': name_en,
                'ru': name_ru,
            },
            'service_description': {
                'default': default_description,
                'en': desc_en,
                'ru': desc_ru,
            },
        }

    def get_app_config_cache_ttl(self) -> int:
        return self.APP_CONFIG_CACHE_TTL

    def is_traffic_selectable(self) -> bool:
        return self.TRAFFIC_SELECTION_MODE.lower() == 'selectable'

    def is_traffic_fixed(self) -> bool:
        """Р’РѕР·РІСЂР°С‰Р°РµС‚ True РµСЃР»Рё РІС‹Р±РѕСЂ С‚СЂР°С„РёРєР° РѕС‚РєР»СЋС‡С‘РЅ (fixed РёР»Рё fixed_with_topup)"""
        return self.TRAFFIC_SELECTION_MODE.lower() in ('fixed', 'fixed_with_topup')

    def is_traffic_topup_blocked(self) -> bool:
        """Р’РѕР·РІСЂР°С‰Р°РµС‚ True РµСЃР»Рё РґРѕРєСѓРїРєР° С‚СЂР°С„РёРєР° РїРѕР»РЅРѕСЃС‚СЊСЋ Р·Р°Р±Р»РѕРєРёСЂРѕРІР°РЅР° (С‚РѕР»СЊРєРѕ fixed)"""
        return self.TRAFFIC_SELECTION_MODE.lower() == 'fixed'

    def get_fixed_traffic_limit(self) -> int:
        return self.FIXED_TRAFFIC_LIMIT_GB

    def is_traffic_topup_enabled(self) -> bool:
        return self.TRAFFIC_TOPUP_ENABLED

    def get_traffic_topup_packages(self) -> list[dict]:
        """Р’РѕР·РІСЂР°С‰Р°РµС‚ РїР°РєРµС‚С‹ РґР»СЏ РґРѕРєСѓРїРєРё С‚СЂР°С„РёРєР°. Р•СЃР»Рё РЅРµ РЅР°СЃС‚СЂРѕРµРЅС‹ - РёСЃРїРѕР»СЊР·СѓРµС‚ TRAFFIC_PACKAGES_CONFIG."""
        config_str = self.TRAFFIC_TOPUP_PACKAGES_CONFIG.strip()

        if not config_str:
            # Р•СЃР»Рё РЅРµ РЅР°СЃС‚СЂРѕРµРЅС‹ РѕС‚РґРµР»СЊРЅС‹Рµ РїР°РєРµС‚С‹ РґР»СЏ РґРѕРєСѓРїРєРё - РёСЃРїРѕР»СЊР·СѓРµРј РѕСЃРЅРѕРІРЅС‹Рµ
            return self.get_traffic_packages()

        packages = []
        for package_config in config_str.split(','):
            package_config = package_config.strip()
            if not package_config:
                continue

            parts = package_config.split(':')
            if len(parts) >= 2:
                try:
                    gb = int(parts[0])
                    price = int(parts[1])
                    enabled = parts[2].lower() == 'true' if len(parts) > 2 else True
                    packages.append({'gb': gb, 'price': price, 'enabled': enabled})
                except (ValueError, IndexError):
                    continue

        return packages or self.get_traffic_packages()

    def get_traffic_topup_price(self, gb: int | None) -> int:
        """Р’РѕР·РІСЂР°С‰Р°РµС‚ С†РµРЅСѓ РґРѕРєСѓРїРєРё РґР»СЏ СѓРєР°Р·Р°РЅРЅРѕРіРѕ РєРѕР»РёС‡РµСЃС‚РІР° Р“Р‘."""
        packages = self.get_traffic_topup_packages()
        enabled_packages = [pkg for pkg in packages if pkg['enabled']]

        if not enabled_packages:
            return 0

        # РС‰РµРј С‚РѕС‡РЅРѕРµ СЃРѕРІРїР°РґРµРЅРёРµ
        for pkg in enabled_packages:
            if pkg['gb'] == gb:
                return pkg['price']

        # Р•СЃР»Рё РЅРµ РЅР°С€Р»Рё - РІРѕР·РІСЂР°С‰Р°РµРј 0
        return 0

    def get_traffic_reset_price_mode(self) -> str:
        return self.TRAFFIC_RESET_PRICE_MODE.lower()

    def get_traffic_reset_base_price(self) -> int:
        return self.TRAFFIC_RESET_BASE_PRICE

    def is_devices_selection_enabled(self) -> bool:
        return self.DEVICES_SELECTION_ENABLED

    def get_devices_selection_disabled_amount(self) -> int | None:
        raw_value = self.DEVICES_SELECTION_DISABLED_AMOUNT

        if raw_value in (None, ''):
            return None

        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            logger.warning(
                'РќРµРєРѕСЂСЂРµРєС‚РЅРѕРµ Р·РЅР°С‡РµРЅРёРµ DEVICES_SELECTION_DISABLED_AMOUNT',
                raw_value=raw_value,
            )
            return None

        if value <= 0:
            return None

        return value

    def get_disabled_mode_device_limit(self) -> int | None:
        return self.get_devices_selection_disabled_amount()

    def is_subscription_revoke_enabled(self) -> bool:
        """РџСЂРѕРІРµСЂСЏРµС‚, РІРєР»СЋС‡РµРЅ Р»Рё РїРµСЂРµРІС‹РїСѓСЃРє РїРѕРґРїРёСЃРєРё."""
        return self.SUBSCRIPTION_REVOKE_ENABLED

    def is_multi_tariff_enabled(self) -> bool:
        """РџСЂРѕРІРµСЂСЏРµС‚, РІРєР»СЋС‡РµРЅ Р»Рё РјСѓР»СЊС‚РёС‚Р°СЂРёС„РЅС‹Р№ СЂРµР¶РёРј."""
        return self.MULTI_TARIFF_ENABLED and self.SALES_MODE == 'tariffs'

    def get_max_active_subscriptions(self) -> int:
        """РњР°РєСЃРёРјР°Р»СЊРЅРѕРµ С‡РёСЃР»Рѕ РѕРґРЅРѕРІСЂРµРјРµРЅРЅС‹С… РїРѕРґРїРёСЃРѕРє (>1 С‚РѕР»СЊРєРѕ РІ multi-tariff)."""
        return self.MAX_ACTIVE_SUBSCRIPTIONS if self.is_multi_tariff_enabled() else 1

    def is_tariffs_mode(self) -> bool:
        """РџСЂРѕРІРµСЂСЏРµС‚, РІРєР»СЋС‡РµРЅ Р»Рё СЂРµР¶РёРј РїСЂРѕРґР°Р¶ 'РўР°СЂРёС„С‹'."""
        return self.SALES_MODE == 'tariffs'

    def is_classic_mode(self) -> bool:
        """РџСЂРѕРІРµСЂСЏРµС‚, РІРєР»СЋС‡РµРЅ Р»Рё РєР»Р°СЃСЃРёС‡РµСЃРєРёР№ СЂРµР¶РёРј РїСЂРѕРґР°Р¶."""
        return self.SALES_MODE != 'tariffs'

    def get_sales_mode(self) -> str:
        """Р’РѕР·РІСЂР°С‰Р°РµС‚ С‚РµРєСѓС‰РёР№ СЂРµР¶РёРј РїСЂРѕРґР°Р¶."""
        return self.SALES_MODE if self.SALES_MODE in ('classic', 'tariffs') else 'tariffs'

    def get_trial_tariff_id(self) -> int:
        """Р’РѕР·РІСЂР°С‰Р°РµС‚ ID С‚Р°СЂРёС„Р° РґР»СЏ С‚СЂРёР°Р»Р° (0 = РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ СЃС‚Р°РЅРґР°СЂС‚РЅС‹Рµ РЅР°СЃС‚СЂРѕР№РєРё)."""
        return max(0, self.TRIAL_TARIFF_ID)

    def is_trial_paid_activation_enabled(self) -> bool:
        # TRIAL_PAYMENT_ENABLED - РіР»Р°РІРЅС‹Р№ РїРµСЂРµРєР»СЋС‡Р°С‚РµР»СЊ РїР»Р°С‚РЅРѕР№ Р°РєС‚РёРІР°С†РёРё
        # Р•СЃР»Рё РІС‹РєР»СЋС‡РµРЅ - С‚СЂРёР°Р» Р±РµСЃРїР»Р°С‚РЅС‹Р№, РЅРµР·Р°РІРёСЃРёРјРѕ РѕС‚ С†РµРЅС‹
        if not self.TRIAL_PAYMENT_ENABLED:
            return False
        # Р•СЃР»Рё РІРєР»СЋС‡РµРЅ - РїСЂРѕРІРµСЂСЏРµРј С‡С‚Рѕ С†РµРЅР° > 0
        return self.TRIAL_ACTIVATION_PRICE > 0

    def get_trial_activation_price(self) -> int:
        try:
            value = int(self.TRIAL_ACTIVATION_PRICE)
        except (TypeError, ValueError):
            logger.warning(
                'РќРµРєРѕСЂСЂРµРєС‚РЅРѕРµ Р·РЅР°С‡РµРЅРёРµ TRIAL_ACTIVATION_PRICE',
                TRIAL_ACTIVATION_PRICE=self.TRIAL_ACTIVATION_PRICE,
            )
            return 0

        if value < 0:
            return 0

        return value

    def is_yookassa_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return self.YOOKASSA_SHOP_ID is not None and self.YOOKASSA_SECRET_KEY is not None

    def is_yookassa_enabled(self) -> bool:
        return self.YOOKASSA_ENABLED and self.YOOKASSA_SHOP_ID is not None and self.YOOKASSA_SECRET_KEY is not None

    def get_yookassa_display_name(self) -> str:
        name = (self.YOOKASSA_DISPLAY_NAME or '').strip()
        return name or 'YooKassa'

    def is_nalogo_enabled(self) -> bool:
        return self.NALOGO_ENABLED and self.NALOGO_INN is not None and self.NALOGO_PASSWORD is not None

    def is_support_topup_enabled(self) -> bool:
        return bool(self.SUPPORT_TOPUP_ENABLED)

    def get_yookassa_return_url(self) -> str:
        if self.YOOKASSA_RETURN_URL:
            return self.YOOKASSA_RETURN_URL
        if self.WEBHOOK_URL:
            return f'{self.WEBHOOK_URL}/payment-success'
        return 'https://t.me/'

    def is_cryptobot_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return self.CRYPTOBOT_API_TOKEN is not None

    def is_cryptobot_enabled(self) -> bool:
        return self.CRYPTOBOT_ENABLED and self.CRYPTOBOT_API_TOKEN is not None

    def get_cryptobot_display_name(self) -> str:
        name = (self.CRYPTOBOT_DISPLAY_NAME or '').strip()
        return name or 'CryptoBot'

    def is_heleket_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return self.HELEKET_MERCHANT_ID is not None and self.HELEKET_API_KEY is not None

    def is_heleket_enabled(self) -> bool:
        return self.HELEKET_ENABLED and self.HELEKET_MERCHANT_ID is not None and self.HELEKET_API_KEY is not None

    def get_heleket_display_name(self) -> str:
        name = (self.HELEKET_DISPLAY_NAME or '').strip()
        return name or 'Heleket Crypto'

    def is_mulenpay_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return (
            self.MULENPAY_API_KEY is not None
            and self.MULENPAY_SECRET_KEY is not None
            and self.MULENPAY_SHOP_ID is not None
        )

    def is_tribute_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return bool(self.TRIBUTE_API_KEY)

    def is_mulenpay_enabled(self) -> bool:
        return (
            self.MULENPAY_ENABLED
            and self.MULENPAY_API_KEY is not None
            and self.MULENPAY_SECRET_KEY is not None
            and self.MULENPAY_SHOP_ID is not None
        )

    def get_mulenpay_display_name(self) -> str:
        name = (self.MULENPAY_DISPLAY_NAME or '').strip()
        if not name:
            return 'Mulen Pay'
        return name

    def get_mulenpay_display_name_html(self) -> str:
        return html.escape(self.get_mulenpay_display_name())

    def get_mulenpay_expected_origin(self) -> str | None:
        override = (self.MULENPAY_IFRAME_EXPECTED_ORIGIN or '').strip()
        if override:
            return override

        base_url = (self.MULENPAY_BASE_URL or '').strip()
        if not base_url:
            return None

        parsed = urlparse(base_url)
        if parsed.scheme and parsed.netloc:
            return f'{parsed.scheme}://{parsed.netloc}'
        return None

    def is_pal24_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return self.PAL24_API_TOKEN is not None and self.PAL24_SHOP_ID is not None

    def is_pal24_enabled(self) -> bool:
        return self.PAL24_ENABLED and self.PAL24_API_TOKEN is not None and self.PAL24_SHOP_ID is not None

    def get_pal24_display_name(self) -> str:
        name = (self.PAL24_DISPLAY_NAME or '').strip()
        return name or 'PAL24'

    def is_platega_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return self.PLATEGA_MERCHANT_ID is not None and self.PLATEGA_SECRET is not None

    def is_platega_enabled(self) -> bool:
        return self.PLATEGA_ENABLED and self.PLATEGA_MERCHANT_ID is not None and self.PLATEGA_SECRET is not None

    def is_platega_recurrent_enabled(self) -> bool:
        return self.is_platega_enabled() and self.PLATEGA_RECURRENT_ENABLED

    def get_platega_display_name(self) -> str:
        name = (self.PLATEGA_DISPLAY_NAME or '').strip()
        if not name:
            return 'Platega'
        return name

    def get_platega_display_name_html(self) -> str:
        return html.escape(self.get_platega_display_name())

    def get_platega_return_url(self) -> str | None:
        if self.PLATEGA_RETURN_URL:
            return self.PLATEGA_RETURN_URL
        if self.WEBHOOK_URL:
            return f'{self.WEBHOOK_URL}/payment-success'
        return None

    def get_platega_failed_url(self) -> str | None:
        if self.PLATEGA_FAILED_URL:
            return self.PLATEGA_FAILED_URL
        if self.WEBHOOK_URL:
            return f'{self.WEBHOOK_URL}/payment-failed'
        return None

    def get_platega_active_methods(self) -> list[int]:
        raw_value = str(self.PLATEGA_ACTIVE_METHODS or '')
        normalized = raw_value.replace(';', ',')
        methods: list[int] = []
        seen: set[int] = set()
        for part in normalized.split(','):
            part = part.strip()
            if not part:
                continue
            try:
                method_code = int(part)
            except ValueError:
                logger.warning('РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ РєРѕРґ РјРµС‚РѕРґР° Platega', part=part)
                continue
            if method_code in {2, 11, 12, 13} and method_code not in seen:
                methods.append(method_code)
                seen.add(method_code)

        if not methods:
            return [2]

        return methods

    @staticmethod
    def get_platega_method_definitions() -> dict[int, dict[str, str]]:
        return {
            2: {
                'name': 'РЎР‘Рџ (QR)',
                'title': "<tg-emoji emoji-id='5886306834410640699'>рџ†•</tg-emoji> РЎР‘Рџ (QR)",
            },
            10: {
                'name': 'Р‘Р°РЅРєРѕРІСЃРєРёРµ РєР°СЂС‚С‹ (RUB)',
                'title': "<tg-emoji emoji-id='5927169041595634481'>рџ’і</tg-emoji> РљР°СЂС‚С‹ (RUB)",
            },
            11: {
                'name': 'РљР°СЂС‚С‹ (RUB)',
                'title': "<tg-emoji emoji-id='5927169041595634481'>рџ’і</tg-emoji> РљР°СЂС‚С‹ (RUB)",
            },
            12: {
                'name': 'РњРµР¶РґСѓРЅР°СЂРѕРґРЅС‹Рµ РєР°СЂС‚С‹',
                'title': "<tg-emoji emoji-id='5927169041595634481'>рџ’і</tg-emoji> РњРµР¶РґСѓРЅР°СЂРѕРґРЅС‹Рµ РєР°СЂС‚С‹",
            },
            13: {
                'name': 'РљСЂРёРїС‚РѕРІР°Р»СЋС‚Р°',
                'title': "<tg-emoji emoji-id='5771755323572359189'>рџ’Ћ</tg-emoji> РљСЂРёРїС‚РѕРІР°Р»СЋС‚Р°",
            },
        }

    def get_platega_method_display_name(self, method_code: int) -> str:
        definitions = self.get_platega_method_definitions()
        info = definitions.get(method_code)
        if info and info.get('name'):
            return info['name']
        return f'РњРµС‚РѕРґ {method_code}'

    def get_platega_method_display_title(self, method_code: int) -> str:
        definitions = self.get_platega_method_definitions()
        info = definitions.get(method_code)
        if not info:
            return f'Platega {method_code}'
        return info.get('title') or info.get('name') or f'Platega {method_code}'

    def is_wata_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return self.WATA_ACCESS_TOKEN is not None

    def is_wata_enabled(self) -> bool:
        return self.WATA_ENABLED and self.WATA_ACCESS_TOKEN is not None

    def get_wata_display_name(self) -> str:
        name = (self.WATA_DISPLAY_NAME or '').strip()
        return name or 'Wata'

    def is_cloudpayments_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return self.CLOUDPAYMENTS_PUBLIC_ID is not None and self.CLOUDPAYMENTS_API_SECRET is not None

    def is_cloudpayments_enabled(self) -> bool:
        return (
            self.CLOUDPAYMENTS_ENABLED
            and self.CLOUDPAYMENTS_PUBLIC_ID is not None
            and self.CLOUDPAYMENTS_API_SECRET is not None
        )

    def get_cloudpayments_display_name(self) -> str:
        name = (self.CLOUDPAYMENTS_DISPLAY_NAME or '').strip()
        return name or 'CloudPayments'

    def is_freekassa_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return (
            self.FREEKASSA_SHOP_ID is not None
            and self.FREEKASSA_API_KEY is not None
            and self.FREEKASSA_SECRET_WORD_1 is not None
            and self.FREEKASSA_SECRET_WORD_2 is not None
        )

    def is_freekassa_enabled(self) -> bool:
        return (
            self.FREEKASSA_ENABLED
            and self.FREEKASSA_SHOP_ID is not None
            and self.FREEKASSA_API_KEY is not None
            and self.FREEKASSA_SECRET_WORD_1 is not None
            and self.FREEKASSA_SECRET_WORD_2 is not None
        )

    def get_freekassa_display_name(self) -> str:
        name = (self.FREEKASSA_DISPLAY_NAME or '').strip()
        return name or 'Freekassa'

    def get_freekassa_display_name_html(self) -> str:
        return html.escape(self.get_freekassa_display_name())

    def is_freekassa_sbp_enabled(self) -> bool:
        return self.FREEKASSA_SBP_ENABLED and self.is_freekassa_enabled()

    def get_freekassa_sbp_display_name(self) -> str:
        name = (self.FREEKASSA_SBP_DISPLAY_NAME or '').strip()
        return name or 'РЎР‘Рџ (QR РєРѕРґ)'

    def get_freekassa_sbp_display_name_html(self) -> str:
        return html.escape(self.get_freekassa_sbp_display_name())

    def is_freekassa_card_enabled(self) -> bool:
        return self.FREEKASSA_CARD_ENABLED and self.is_freekassa_enabled()

    def get_freekassa_card_display_name(self) -> str:
        name = (self.FREEKASSA_CARD_DISPLAY_NAME or '').strip()
        return name or 'РљР°СЂС‚Р° Р Р¤'

    def get_freekassa_card_display_name_html(self) -> str:
        return html.escape(self.get_freekassa_card_display_name())

    def is_kassa_ai_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return (
            self.KASSA_AI_SHOP_ID is not None
            and self.KASSA_AI_API_KEY is not None
            and self.KASSA_AI_SECRET_WORD_2 is not None
        )

    def is_kassa_ai_enabled(self) -> bool:
        return (
            self.KASSA_AI_ENABLED
            and self.KASSA_AI_SHOP_ID is not None
            and self.KASSA_AI_API_KEY is not None
            and self.KASSA_AI_SECRET_WORD_2 is not None
        )

    def get_kassa_ai_display_name(self) -> str:
        name = (self.KASSA_AI_DISPLAY_NAME or '').strip()
        return name or 'KassaAI'

    def get_kassa_ai_display_name_html(self) -> str:
        return html.escape(self.get_kassa_ai_display_name())

    def is_riopay_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return self.RIOPAY_API_TOKEN is not None

    def is_riopay_enabled(self) -> bool:
        return self.RIOPAY_ENABLED and self.RIOPAY_API_TOKEN is not None

    def get_riopay_display_name(self) -> str:
        name = (self.RIOPAY_DISPLAY_NAME or '').strip()
        return name or 'RioPay'

    def get_riopay_display_name_html(self) -> str:
        return html.escape(self.get_riopay_display_name())

    def is_severpay_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return self.SEVERPAY_MID is not None and self.SEVERPAY_TOKEN is not None

    def is_severpay_enabled(self) -> bool:
        return self.SEVERPAY_ENABLED and self.SEVERPAY_MID is not None and self.SEVERPAY_TOKEN is not None

    def get_severpay_display_name(self) -> str:
        name = (self.SEVERPAY_DISPLAY_NAME or '').strip()
        return name or 'SeverPay'

    def get_severpay_display_name_html(self) -> str:
        return html.escape(self.get_severpay_display_name())

    def is_apple_iap_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        environment = self.get_apple_iap_environment()
        return (
            bool((self.APPLE_IAP_KEY_ID or '').strip())
            and bool((self.APPLE_IAP_ISSUER_ID or '').strip())
            and bool((self.APPLE_IAP_BUNDLE_ID or '').strip())
            and environment in {'Sandbox', 'Production'}
            and (environment != 'Production' or self.APPLE_IAP_APP_APPLE_ID is not None)
            and bool(self.get_apple_iap_root_cert_paths())
            and bool(self.get_apple_iap_private_key())
        )

    def is_apple_iap_enabled(self) -> bool:
        environment = self.get_apple_iap_environment()
        return (
            self.APPLE_IAP_ENABLED
            and bool((self.APPLE_IAP_KEY_ID or '').strip())
            and bool((self.APPLE_IAP_ISSUER_ID or '').strip())
            and bool((self.APPLE_IAP_BUNDLE_ID or '').strip())
            and environment in {'Sandbox', 'Production'}
            and (environment != 'Production' or self.APPLE_IAP_APP_APPLE_ID is not None)
            and bool(self.get_apple_iap_root_cert_paths())
            and bool(self.get_apple_iap_private_key())
        )

    def get_apple_iap_environment(self) -> Literal['Sandbox', 'Production']:
        environment = (self.APPLE_IAP_ENVIRONMENT or '').strip()
        if environment == 'Sandbox':
            return 'Sandbox'
        return 'Production'

    def get_apple_iap_root_cert_paths(self) -> list[Path]:
        return [Path(path.strip()) for path in (self.APPLE_IAP_ROOT_CERTS_PATHS or '').split(',') if path.strip()]

    def get_apple_iap_products(self) -> dict[str, int]:
        """Return mapping of Apple product ID -> kopeks amount."""
        import json as _json

        try:
            products = _json.loads(self.APPLE_IAP_PRODUCTS)
            if not isinstance(products, dict):
                return {}
            normalized: dict[str, int] = {}
            for product_id, amount_kopeks in products.items():
                try:
                    amount = int(amount_kopeks)
                except (TypeError, ValueError):
                    continue
                product = str(product_id).strip()
                if product and amount > 0:
                    normalized[product] = amount
            return normalized
        except (TypeError, _json.JSONDecodeError):
            return {}

    def get_apple_iap_private_key(self) -> str | None:
        """Return the .p8 private key contents."""
        if self.APPLE_IAP_PRIVATE_KEY:
            return self.APPLE_IAP_PRIVATE_KEY
        if self.APPLE_IAP_PRIVATE_KEY_PATH:
            key_path = Path(self.APPLE_IAP_PRIVATE_KEY_PATH)
            try:
                return key_path.read_text().strip()
            except (OSError, UnicodeDecodeError) as error:
                logger.error(
                    'Failed to load Apple IAP private key file',
                    path=str(key_path),
                    error=str(error),
                    exc_info=True,
                )
                return None
        return None

    def is_paypear_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return self.PAYPEAR_SHOP_ID is not None and self.PAYPEAR_SECRET_KEY is not None

    def is_paypear_enabled(self) -> bool:
        return self.PAYPEAR_ENABLED and self.PAYPEAR_SHOP_ID is not None and self.PAYPEAR_SECRET_KEY is not None

    def get_paypear_display_name(self) -> str:
        name = (self.PAYPEAR_DISPLAY_NAME or '').strip()
        return name or 'PayPear'

    def get_paypear_display_name_html(self) -> str:
        return html.escape(self.get_paypear_display_name())

    def is_rollypay_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return self.ROLLYPAY_API_KEY is not None and self.ROLLYPAY_SIGNING_SECRET is not None

    def is_rollypay_enabled(self) -> bool:
        return self.ROLLYPAY_ENABLED and self.ROLLYPAY_API_KEY is not None and self.ROLLYPAY_SIGNING_SECRET is not None

    def get_rollypay_display_name(self) -> str:
        name = (self.ROLLYPAY_DISPLAY_NAME or '').strip()
        return name or 'RollyPay'

    def get_rollypay_display_name_html(self) -> str:
        return html.escape(self.get_rollypay_display_name())

    def is_overpay_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return (
            self.OVERPAY_USERNAME is not None
            and self.OVERPAY_PASSWORD is not None
            and self.OVERPAY_PROJECT_ID is not None
        )

    def is_overpay_enabled(self) -> bool:
        return (
            self.OVERPAY_ENABLED
            and self.OVERPAY_USERNAME is not None
            and self.OVERPAY_PASSWORD is not None
            and self.OVERPAY_PROJECT_ID is not None
        )

    def get_overpay_display_name(self) -> str:
        name = (self.OVERPAY_DISPLAY_NAME or '').strip()
        return name or 'Overpay'

    def get_overpay_display_name_html(self) -> str:
        return html.escape(self.get_overpay_display_name())

    def get_overpay_terminal_id(self, option: str | None = None) -> str | None:
        terminals = {
            'fps': self.OVERPAY_SBP_TERMINAL_ID,
            'card': self.OVERPAY_CARD_TERMINAL_ID,
            'int': self.OVERPAY_INT_TERMINAL_ID,
        }
        return terminals.get(option or '') or self.OVERPAY_PROJECT_ID

    def is_overpay_int_enabled(self) -> bool:
        return self.is_overpay_enabled() and self.OVERPAY_INT_ENABLED and self.OVERPAY_RUB_PER_EUR > 0

    def is_overpay_sbp_direct_qr_enabled(self) -> bool:
        return self.OVERPAY_SBP_DIRECT_QR and bool((self.OVERPAY_SERVER_IP or '').strip())

    def is_aurapay_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return (
            self.AURAPAY_API_KEY is not None
            and self.AURAPAY_SHOP_ID is not None
            and self.AURAPAY_SECRET_KEY is not None
        )

    def is_aurapay_enabled(self) -> bool:
        return (
            self.AURAPAY_ENABLED
            and self.AURAPAY_API_KEY is not None
            and self.AURAPAY_SHOP_ID is not None
            and self.AURAPAY_SECRET_KEY is not None
        )

    def get_aurapay_display_name(self) -> str:
        name = (self.AURAPAY_DISPLAY_NAME or '').strip()
        return name or 'AuraPay'

    def get_aurapay_display_name_html(self) -> str:
        return html.escape(self.get_aurapay_display_name())

    def is_aurapay_sbp_enabled(self) -> bool:
        return self.AURAPAY_SBP_ENABLED and self.is_aurapay_enabled()

    def get_aurapay_sbp_display_name(self) -> str:
        name = (self.AURAPAY_SBP_DISPLAY_NAME or '').strip()
        return name or 'РЎР‘Рџ (AuraPay)'

    def get_aurapay_sbp_display_name_html(self) -> str:
        return html.escape(self.get_aurapay_sbp_display_name())

    def is_aurapay_card_enabled(self) -> bool:
        return self.AURAPAY_CARD_ENABLED and self.is_aurapay_enabled()

    def get_aurapay_card_display_name(self) -> str:
        name = (self.AURAPAY_CARD_DISPLAY_NAME or '').strip()
        return name or 'РљР°СЂС‚Р° (AuraPay)'

    def get_aurapay_card_display_name_html(self) -> str:
        return html.escape(self.get_aurapay_card_display_name())

    def is_antilopay_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return (
            self.ANTILOPAY_SECRET_ID is not None
            and self.ANTILOPAY_PRIVATE_KEY is not None
            and self.ANTILOPAY_PUBLIC_KEY is not None
            and self.ANTILOPAY_PROJECT_ID is not None
        )

    def is_antilopay_enabled(self) -> bool:
        return (
            self.ANTILOPAY_ENABLED
            and self.ANTILOPAY_SECRET_ID is not None
            and self.ANTILOPAY_PRIVATE_KEY is not None
            and self.ANTILOPAY_PUBLIC_KEY is not None
            and self.ANTILOPAY_PROJECT_ID is not None
        )

    def get_antilopay_display_name(self) -> str:
        name = (self.ANTILOPAY_DISPLAY_NAME or '').strip()
        return name or 'Antilopay'

    def get_antilopay_display_name_html(self) -> str:
        return html.escape(self.get_antilopay_display_name())

    def is_antilopay_sbp_enabled(self) -> bool:
        return self.ANTILOPAY_SBP_ENABLED and self.is_antilopay_enabled()

    def get_antilopay_sbp_display_name(self) -> str:
        name = (self.ANTILOPAY_SBP_DISPLAY_NAME or '').strip()
        return name or 'РЎР‘Рџ (Antilopay)'

    def get_antilopay_sbp_display_name_html(self) -> str:
        return html.escape(self.get_antilopay_sbp_display_name())

    def is_antilopay_card_enabled(self) -> bool:
        return self.ANTILOPAY_CARD_ENABLED and self.is_antilopay_enabled()

    def get_antilopay_card_display_name(self) -> str:
        name = (self.ANTILOPAY_CARD_DISPLAY_NAME or '').strip()
        return name or 'РљР°СЂС‚Р° (Antilopay)'

    def get_antilopay_card_display_name_html(self) -> str:
        return html.escape(self.get_antilopay_card_display_name())

    def is_antilopay_sberpay_enabled(self) -> bool:
        return self.ANTILOPAY_SBERPAY_ENABLED and self.is_antilopay_enabled()

    def get_antilopay_sberpay_display_name(self) -> str:
        name = (self.ANTILOPAY_SBERPAY_DISPLAY_NAME or '').strip()
        return name or 'SberPay (Antilopay)'

    def get_antilopay_sberpay_display_name_html(self) -> str:
        return html.escape(self.get_antilopay_sberpay_display_name())

    def is_jupiter_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return self.JUPITER_TOKEN is not None and self.JUPITER_SECRET is not None

    def is_jupiter_enabled(self) -> bool:
        return self.JUPITER_ENABLED and self.JUPITER_TOKEN is not None and self.JUPITER_SECRET is not None

    def get_jupiter_display_name(self) -> str:
        name = (self.JUPITER_DISPLAY_NAME or '').strip()
        return name or 'Jupiter'

    def get_jupiter_display_name_html(self) -> str:
        return html.escape(self.get_jupiter_display_name())

    def is_jupiter_sbp_enabled(self) -> bool:
        return self.JUPITER_SBP_ENABLED and self.is_jupiter_enabled()

    def get_jupiter_sbp_display_name(self) -> str:
        name = (self.JUPITER_SBP_DISPLAY_NAME or '').strip()
        return name or 'РЎР‘Рџ (Jupiter)'

    def get_jupiter_sbp_display_name_html(self) -> str:
        return html.escape(self.get_jupiter_sbp_display_name())

    def is_cispay_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return bool(self.CISPAY_SHOP_ID and self.CISPAY_API_KEY)

    def is_cispay_enabled(self) -> bool:
        # РџСѓСЃС‚Р°СЏ СЃС‚СЂРѕРєР° С‚Р°Рє Р¶Рµ РЅРµРїСЂРёРіРѕРґРЅР°, РєР°Рє None: СЃ РїСѓСЃС‚С‹Рј РєР»СЋС‡РѕРј HMAC РІРµР±С…СѓРєР°
        # С‚СЂРёРІРёР°Р»СЊРЅРѕ РїРѕРґРґРµР»С‹РІР°РµС‚СЃСЏ, РїРѕСЌС‚РѕРјСѓ РІРєР»СЋС‡Р°РµРј С‚РѕР»СЊРєРѕ РїСЂРё РЅРµРїСѓСЃС‚С‹С… Р·РЅР°С‡РµРЅРёСЏС….
        return bool(self.CISPAY_ENABLED and self.CISPAY_SHOP_ID and self.CISPAY_API_KEY)

    def get_cispay_display_name(self) -> str:
        name = (self.CISPAY_DISPLAY_NAME or '').strip()
        return name or 'CisPay'

    def get_cispay_display_name_html(self) -> str:
        return html.escape(self.get_cispay_display_name())

    def is_cispay_card_enabled(self) -> bool:
        return self.CISPAY_CARD_ENABLED and self.is_cispay_enabled()

    def get_cispay_card_display_name(self) -> str:
        name = (self.CISPAY_CARD_DISPLAY_NAME or '').strip()
        return name or 'РљР°СЂС‚Р° (CisPay)'

    def get_cispay_card_display_name_html(self) -> str:
        return html.escape(self.get_cispay_card_display_name())

    def is_cispay_sbp_enabled(self) -> bool:
        return self.CISPAY_SBP_ENABLED and self.is_cispay_enabled()

    def get_cispay_sbp_display_name(self) -> str:
        name = (self.CISPAY_SBP_DISPLAY_NAME or '').strip()
        return name or 'РЎР‘Рџ (CisPay)'

    def get_cispay_sbp_display_name_html(self) -> str:
        return html.escape(self.get_cispay_sbp_display_name())

    def is_donut_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return self.DONUT_TOKEN is not None and self.DONUT_SECRET is not None

    def is_donut_enabled(self) -> bool:
        return self.DONUT_ENABLED and self.DONUT_TOKEN is not None and self.DONUT_SECRET is not None

    def get_donut_display_name(self) -> str:
        name = (self.DONUT_DISPLAY_NAME or '').strip()
        return name or 'Donut'

    def get_donut_display_name_html(self) -> str:
        return html.escape(self.get_donut_display_name())

    def is_donut_card_enabled(self) -> bool:
        return self.DONUT_CARD_ENABLED and self.is_donut_enabled()

    def get_donut_card_display_name(self) -> str:
        name = (self.DONUT_CARD_DISPLAY_NAME or '').strip()
        return name or 'РљР°СЂС‚Р° (Donut)'

    def get_donut_card_display_name_html(self) -> str:
        return html.escape(self.get_donut_card_display_name())

    def is_donut_sbp_enabled(self) -> bool:
        return self.DONUT_SBP_ENABLED and self.is_donut_enabled()

    def get_donut_sbp_display_name(self) -> str:
        name = (self.DONUT_SBP_DISPLAY_NAME or '').strip()
        return name or 'РЎР‘Рџ (Donut)'

    def get_donut_sbp_display_name_html(self) -> str:
        return html.escape(self.get_donut_sbp_display_name())

    def is_donut_sbp_qr_enabled(self) -> bool:
        return self.DONUT_SBP_QR_ENABLED and self.is_donut_enabled()

    def get_donut_sbp_qr_display_name(self) -> str:
        name = (self.DONUT_SBP_QR_DISPLAY_NAME or '').strip()
        return name or 'РЎР‘Рџ QR (Donut)'

    def get_donut_sbp_qr_display_name_html(self) -> str:
        return html.escape(self.get_donut_sbp_qr_display_name())

    def is_lava_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return (
            self.LAVA_SHOP_ID is not None and self.LAVA_SECRET_KEY is not None and self.LAVA_WEBHOOK_SECRET is not None
        )

    def is_lava_enabled(self) -> bool:
        return (
            self.LAVA_ENABLED
            and self.LAVA_SHOP_ID is not None
            and self.LAVA_SECRET_KEY is not None
            and self.LAVA_WEBHOOK_SECRET is not None
        )

    def is_lava_recurrent_enabled(self) -> bool:
        return self.LAVA_RECURRENT_ENABLED and self.is_lava_enabled()

    def get_lava_display_name(self) -> str:
        name = (self.LAVA_DISPLAY_NAME or '').strip()
        return name or 'Lava'

    def get_lava_display_name_html(self) -> str:
        return html.escape(self.get_lava_display_name())

    def is_lava_card_enabled(self) -> bool:
        return self.LAVA_CARD_ENABLED and self.is_lava_enabled()

    def get_lava_card_display_name(self) -> str:
        name = (self.LAVA_CARD_DISPLAY_NAME or '').strip()
        return name or 'РљР°СЂС‚Р° (Lava)'

    def get_lava_card_display_name_html(self) -> str:
        return html.escape(self.get_lava_card_display_name())

    def is_lava_sbp_enabled(self) -> bool:
        return self.LAVA_SBP_ENABLED and self.is_lava_enabled()

    def get_lava_sbp_display_name(self) -> str:
        name = (self.LAVA_SBP_DISPLAY_NAME or '').strip()
        return name or 'РЎР‘Рџ (Lava)'

    def get_lava_sbp_display_name_html(self) -> str:
        return html.escape(self.get_lava_sbp_display_name())

    def is_cispay_enabled(self) -> bool:
        return bool(self.CISPAY_ENABLED and self.CISPAY_SHOP_ID and self.CISPAY_API_KEY)

    def get_cispay_display_name(self) -> str:
        name = (self.CISPAY_DISPLAY_NAME or '').strip()
        return name or 'CisPay'

    def get_cispay_display_name_html(self) -> str:
        return html.escape(self.get_cispay_display_name())

    def is_cispay_card_enabled(self) -> bool:
        return self.CISPAY_CARD_ENABLED and self.is_cispay_enabled()

    def get_cispay_card_display_name(self) -> str:
        name = (self.CISPAY_CARD_DISPLAY_NAME or '').strip()
        return name or 'РљР°СЂС‚Р° (CisPay)'

    def get_cispay_card_display_name_html(self) -> str:
        return html.escape(self.get_cispay_card_display_name())

    def is_cispay_sbp_enabled(self) -> bool:
        return self.CISPAY_SBP_ENABLED and self.is_cispay_enabled()

    def get_cispay_sbp_display_name(self) -> str:
        name = (self.CISPAY_SBP_DISPLAY_NAME or '').strip()
        return name or 'РЎР‘Рџ (CisPay)'

    def get_cispay_sbp_display_name_html(self) -> str:
        return html.escape(self.get_cispay_sbp_display_name())

    def is_etoplatezhi_configured(self) -> bool:
        """Р•СЃС‚СЊ Р»Рё СѓС‡С‘С‚РЅС‹Рµ РґР°РЅРЅС‹Рµ РїСЂРѕРІР°Р№РґРµСЂР° вЂ” Р±РµР· СѓС‡С‘С‚Р° С„Р»Р°РіР° РІРєР»СЋС‡РµРЅРёСЏ."""
        return self.ETOPLATEZHI_PROJECT_ID is not None and self.ETOPLATEZHI_SECRET_KEY is not None

    def is_etoplatezhi_enabled(self) -> bool:
        return (
            self.ETOPLATEZHI_ENABLED
            and self.ETOPLATEZHI_PROJECT_ID is not None
            and self.ETOPLATEZHI_SECRET_KEY is not None
        )

    def get_etoplatezhi_display_name(self) -> str:
        name = (self.ETOPLATEZHI_DISPLAY_NAME or '').strip()
        return name or 'Etoplatezhi'

    def get_etoplatezhi_display_name_html(self) -> str:
        return html.escape(self.get_etoplatezhi_display_name())

    def is_etoplatezhi_sbp_enabled(self) -> bool:
        return self.ETOPLATEZHI_SBP_ENABLED and self.is_etoplatezhi_enabled()

    def get_etoplatezhi_sbp_display_name(self) -> str:
        name = (self.ETOPLATEZHI_SBP_DISPLAY_NAME or '').strip()
        return name or 'РЎР‘Рџ (Etoplatezhi)'

    def get_etoplatezhi_sbp_display_name_html(self) -> str:
        return html.escape(self.get_etoplatezhi_sbp_display_name())

    def is_etoplatezhi_card_enabled(self) -> bool:
        return self.ETOPLATEZHI_CARD_ENABLED and self.is_etoplatezhi_enabled()

    def get_etoplatezhi_card_display_name(self) -> str:
        name = (self.ETOPLATEZHI_CARD_DISPLAY_NAME or '').strip()
        return name or 'РљР°СЂС‚Р° (Etoplatezhi)'

    def get_etoplatezhi_card_display_name_html(self) -> str:
        return html.escape(self.get_etoplatezhi_card_display_name())

    def is_kassa_ai_sbp_enabled(self) -> bool:
        return self.KASSA_AI_SBP_ENABLED and self.is_kassa_ai_enabled()

    def get_kassa_ai_sbp_display_name(self) -> str:
        name = (self.KASSA_AI_SBP_DISPLAY_NAME or '').strip()
        return name or 'РЎР‘Рџ (KassaAI)'

    def get_kassa_ai_sbp_display_name_html(self) -> str:
        return html.escape(self.get_kassa_ai_sbp_display_name())

    def is_kassa_ai_card_enabled(self) -> bool:
        return self.KASSA_AI_CARD_ENABLED and self.is_kassa_ai_enabled()

    def get_kassa_ai_card_display_name(self) -> str:
        name = (self.KASSA_AI_CARD_DISPLAY_NAME or '').strip()
        return name or 'РљР°СЂС‚Р° (KassaAI)'

    def get_kassa_ai_card_display_name_html(self) -> str:
        return html.escape(self.get_kassa_ai_card_display_name())

    def is_kassa_ai_sberpay_enabled(self) -> bool:
        return self.KASSA_AI_SBERPAY_ENABLED and self.is_kassa_ai_enabled()

    def get_kassa_ai_sberpay_display_name(self) -> str:
        name = (self.KASSA_AI_SBERPAY_DISPLAY_NAME or '').strip()
        return name or 'SberPay (KassaAI)'

    def get_kassa_ai_sberpay_display_name_html(self) -> str:
        return html.escape(self.get_kassa_ai_sberpay_display_name())

    def is_payment_verification_auto_check_enabled(self) -> bool:
        return self.PAYMENT_VERIFICATION_AUTO_CHECK_ENABLED

    def get_payment_verification_auto_check_interval(self) -> int:
        try:
            minutes = int(self.PAYMENT_VERIFICATION_AUTO_CHECK_INTERVAL_MINUTES)
        except (
            TypeError,
            ValueError,
        ):  # pragma: no cover - Р·Р°С‰РёС‚РЅР°СЏ РїСЂРѕРІРµСЂРєР° РєРѕРЅС„РёРіСѓСЂР°С†РёРё
            minutes = 10

        if minutes <= 0:
            logger.warning(
                'РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ РёРЅС‚РµСЂРІР°Р» Р°РІС‚РѕРїСЂРѕРІРµСЂРєРё РїР»Р°С‚РµР¶РµР№: . РСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ Р·РЅР°С‡РµРЅРёРµ РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ 10 РјРёРЅСѓС‚.',
                PAYMENT_VERIFICATION_AUTO_CHECK_INTERVAL_MINUTES=self.PAYMENT_VERIFICATION_AUTO_CHECK_INTERVAL_MINUTES,
            )
            return 10

        return minutes

    def get_cryptobot_base_url(self) -> str:
        if self.CRYPTOBOT_TESTNET:
            return 'https://testnet-pay.crypt.bot'
        return self.CRYPTOBOT_BASE_URL

    def get_cryptobot_assets(self) -> list[str]:
        try:
            assets = self.CRYPTOBOT_ASSETS.strip()
            if not assets:
                return ['USDT', 'TON']
            return [asset.strip() for asset in assets.split(',') if asset.strip()]
        except (ValueError, AttributeError):
            return ['USDT', 'TON']

    def get_cryptobot_invoice_expires_seconds(self) -> int:
        return self.CRYPTOBOT_INVOICE_EXPIRES_HOURS * 3600

    def get_heleket_markup_percent(self) -> float:
        try:
            return float(self.HELEKET_MARKUP_PERCENT)
        except (TypeError, ValueError):
            return 0.0

    def get_heleket_lifetime(self) -> int:
        try:
            value = int(self.HELEKET_INVOICE_LIFETIME)
        except (TypeError, ValueError):
            value = 3600
        return max(300, min(43200, value))

    def get_heleket_callback_url(self) -> str | None:
        if self.HELEKET_CALLBACK_URL:
            return self.HELEKET_CALLBACK_URL
        if self.WEBHOOK_URL:
            return f'{self.WEBHOOK_URL}{self.HELEKET_WEBHOOK_PATH}'
        return None

    def is_happ_cryptolink_mode(self) -> bool:
        return self.CONNECT_BUTTON_MODE == 'happ_cryptolink'

    def is_happ_download_button_enabled(self) -> bool:
        return self.is_happ_cryptolink_mode() and self.CONNECT_BUTTON_HAPP_DOWNLOAD_ENABLED

    def should_hide_subscription_link(self) -> bool:
        """Returns True when subscription links must be hidden from the interface."""

        if self.is_happ_cryptolink_mode():
            return False
        return self.HIDE_SUBSCRIPTION_LINK

    def is_contests_enabled(self) -> bool:
        if getattr(self, 'CONTESTS_ENABLED', False):
            return True
        # legacy fallback
        return bool(getattr(self, 'REFERRAL_CONTESTS_ENABLED', False))

    def is_referral_contests_enabled(self) -> bool:
        # kept for backward compatibility
        return self.is_contests_enabled()

    def get_happ_cryptolink_redirect_template(self) -> str | None:
        template = (self.HAPP_CRYPTOLINK_REDIRECT_TEMPLATE or '').strip()
        return template or None

    def get_happ_download_link(self, platform: str) -> str | None:
        platform_key = platform.lower()

        if platform_key == 'pc':
            platform_key = 'windows'

        links = {
            'ios': (self.HAPP_DOWNLOAD_LINK_IOS or '').strip(),
            'android': (self.HAPP_DOWNLOAD_LINK_ANDROID or '').strip(),
            'macos': (self.HAPP_DOWNLOAD_LINK_MACOS or '').strip(),
            'windows': ((self.HAPP_DOWNLOAD_LINK_WINDOWS or '').strip() or (self.HAPP_DOWNLOAD_LINK_PC or '').strip()),
        }
        link = links.get(platform_key)
        return link or None

    def is_maintenance_mode(self) -> bool:
        return self.MAINTENANCE_MODE

    def get_maintenance_message(self) -> str:
        return self.MAINTENANCE_MESSAGE

    def get_maintenance_check_interval(self) -> int:
        return self.MAINTENANCE_CHECK_INTERVAL

    def get_maintenance_retry_attempts(self) -> int:
        try:
            attempts = int(self.MAINTENANCE_RETRY_ATTEMPTS)
        except (TypeError, ValueError):
            attempts = 1
        return max(1, attempts)

    def is_base_promo_group_period_discount_enabled(self) -> bool:
        return self.BASE_PROMO_GROUP_PERIOD_DISCOUNTS_ENABLED

    def get_base_promo_group_period_discounts(self) -> dict[int, int]:
        try:
            config_str = (self.BASE_PROMO_GROUP_PERIOD_DISCOUNTS or '').strip()
            if not config_str:
                return {}

            discounts: dict[int, int] = {}
            for part in config_str.split(','):
                part = part.strip()
                if not part:
                    continue

                period_and_discount = part.split(':')
                if len(period_and_discount) != 2:
                    continue

                period_str, discount_str = period_and_discount
                try:
                    period_days = int(period_str.strip())
                    discount_percent = int(discount_str.strip())
                except ValueError:
                    continue

                discounts[period_days] = max(0, min(100, discount_percent))

            return discounts
        except Exception:
            return {}

    def get_base_promo_group_period_discount(self, period_days: int | None) -> int:
        if not period_days or not self.is_base_promo_group_period_discount_enabled():
            return 0

        discounts = self.get_base_promo_group_period_discounts()
        return discounts.get(period_days, 0)

    def is_maintenance_auto_enable(self) -> bool:
        return self.MAINTENANCE_AUTO_ENABLE

    def is_maintenance_monitoring_enabled(self) -> bool:
        return self.MAINTENANCE_MONITORING_ENABLED

    def get_available_subscription_periods(self) -> list[int]:
        """
        Р’РѕР·РІСЂР°С‰Р°РµС‚ РґРѕСЃС‚СѓРїРЅС‹Рµ РїРµСЂРёРѕРґС‹ РїРѕРґРїРёСЃРєРё.
        РСЃРїРѕР»СЊР·СѓРµС‚ AVAILABLE_SUBSCRIPTION_PERIODS РґР»СЏ С„РёР»СЊС‚СЂР°С†РёРё.
        РќРµ С„РёР»СЊС‚СЂСѓРµС‚ РїРѕ С†РµРЅРµ, С‚.Рє. РІ СЂРµР¶РёРјРµ classic Р±Р°Р·РѕРІР°СЏ С†РµРЅР° РјРѕР¶РµС‚ Р±С‹С‚СЊ 0.
        """

        # РџРѕР»СѓС‡Р°РµРј СЂР°Р·СЂРµС€С‘РЅРЅС‹Рµ РїРµСЂРёРѕРґС‹ РёР· РЅР°СЃС‚СЂРѕР№РєРё
        try:
            periods_str = self.AVAILABLE_SUBSCRIPTION_PERIODS
            if not periods_str or not periods_str.strip():
                allowed_periods = {14, 30, 60, 90, 180, 360}
            else:
                allowed_periods = set()
                for period_str in periods_str.split(','):
                    period_str = period_str.strip()
                    if period_str:
                        allowed_periods.add(int(period_str))
        except (ValueError, AttributeError):
            allowed_periods = {14, 30, 60, 90, 180, 360}

        # Р’РѕР·РІСЂР°С‰Р°РµРј С‚РѕР»СЊРєРѕ СЂР°Р·СЂРµС€С‘РЅРЅС‹Рµ РїРµСЂРёРѕРґС‹ (Р±РµР· С„РёР»СЊС‚СЂР°С†РёРё РїРѕ С†РµРЅРµ,
        # С‚.Рє. РІ СЂРµР¶РёРјРµ classic С†РµРЅР° СЃРєР»Р°РґС‹РІР°РµС‚СЃСЏ РёР· СЃРµСЂРІРµСЂРѕРІ/С‚СЂР°С„РёРєР°/СѓСЃС‚СЂРѕР№СЃС‚РІ)
        periods = sorted(allowed_periods)

        return periods or [30, 90, 180]

    def get_available_renewal_periods(self) -> list[int]:
        """
        Р’РѕР·РІСЂР°С‰Р°РµС‚ РґРѕСЃС‚СѓРїРЅС‹Рµ РїРµСЂРёРѕРґС‹ РїСЂРѕРґР»РµРЅРёСЏ.
        РСЃРїРѕР»СЊР·СѓРµС‚ AVAILABLE_RENEWAL_PERIODS РґР»СЏ С„РёР»СЊС‚СЂР°С†РёРё.
        РќРµ С„РёР»СЊС‚СЂСѓРµС‚ РїРѕ С†РµРЅРµ, С‚.Рє. РІ СЂРµР¶РёРјРµ classic Р±Р°Р·РѕРІР°СЏ С†РµРЅР° РјРѕР¶РµС‚ Р±С‹С‚СЊ 0.
        """
        # РџРѕР»СѓС‡Р°РµРј СЂР°Р·СЂРµС€С‘РЅРЅС‹Рµ РїРµСЂРёРѕРґС‹ РёР· РЅР°СЃС‚СЂРѕР№РєРё
        try:
            periods_str = self.AVAILABLE_RENEWAL_PERIODS
            if not periods_str or not periods_str.strip():
                allowed_periods = {30, 60, 90, 180, 360}
            else:
                allowed_periods = set()
                for period_str in periods_str.split(','):
                    period_str = period_str.strip()
                    if period_str:
                        allowed_periods.add(int(period_str))
        except (ValueError, AttributeError):
            allowed_periods = {30, 60, 90, 180, 360}

        # Р’РѕР·РІСЂР°С‰Р°РµРј С‚РѕР»СЊРєРѕ СЂР°Р·СЂРµС€С‘РЅРЅС‹Рµ РїРµСЂРёРѕРґС‹ (Р±РµР· С„РёР»СЊС‚СЂР°С†РёРё РїРѕ С†РµРЅРµ)
        periods = sorted(allowed_periods)

        return periods or [30, 90, 180]

    def get_configured_subscription_periods(self) -> list[int]:
        """
        Р’РѕР·РІСЂР°С‰Р°РµС‚ РЅР°СЃС‚СЂРѕРµРЅРЅС‹Рµ РїРµСЂРёРѕРґС‹ РїРѕРґРїРёСЃРєРё РёР· AVAILABLE_SUBSCRIPTION_PERIODS.
        Р‘Р•Р— С„РёР»СЊС‚СЂР°С†РёРё РїРѕ С†РµРЅР°Рј - РёСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ РґР»СЏ Р°РґРјРёРЅРєРё.
        """
        try:
            periods_str = self.AVAILABLE_SUBSCRIPTION_PERIODS
            if not periods_str or not periods_str.strip():
                return [14, 30, 60, 90, 180, 360]

            periods = []
            for period_str in periods_str.split(','):
                period_str = period_str.strip()
                if period_str:
                    periods.append(int(period_str))
            return sorted(periods) if periods else [14, 30, 60, 90, 180, 360]
        except (ValueError, AttributeError):
            return [14, 30, 60, 90, 180, 360]

    def get_configured_renewal_periods(self) -> list[int]:
        """
        Р’РѕР·РІСЂР°С‰Р°РµС‚ РЅР°СЃС‚СЂРѕРµРЅРЅС‹Рµ РїРµСЂРёРѕРґС‹ РїСЂРѕРґР»РµРЅРёСЏ РёР· AVAILABLE_RENEWAL_PERIODS.
        Р‘Р•Р— С„РёР»СЊС‚СЂР°С†РёРё РїРѕ С†РµРЅР°Рј - РёСЃРїРѕР»СЊР·СѓРµС‚СЃСЏ РґР»СЏ Р°РґРјРёРЅРєРё.
        """
        try:
            periods_str = self.AVAILABLE_RENEWAL_PERIODS
            if not periods_str or not periods_str.strip():
                return [30, 60, 90, 180, 360]

            periods = []
            for period_str in periods_str.split(','):
                period_str = period_str.strip()
                if period_str:
                    periods.append(int(period_str))
            return sorted(periods) if periods else [30, 60, 90, 180, 360]
        except (ValueError, AttributeError):
            return [30, 60, 90, 180, 360]

    def get_balance_payment_description(
        self,
        amount_kopeks: int,
        telegram_user_id: int | None = None,
        user_db_id: int | None = None,
    ) -> str:
        # Р‘Р°Р·РѕРІРѕРµ РѕРїРёСЃР°РЅРёРµ
        description = f'{self.PAYMENT_BALANCE_DESCRIPTION} РЅР° {self.format_price(amount_kopeks)}'

        # Р”РѕР±Р°РІР»СЏРµРј РёРґРµРЅС‚РёС„РёРєР°С‚РѕСЂ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ (TG ID РїСЂРёРѕСЂРёС‚РµС‚, fallback РЅР° DB ID)
        if telegram_user_id is not None:
            description += f' (ID {telegram_user_id})'
        elif user_db_id is not None:
            description += f' (U{user_db_id})'

        # Р¤РѕСЂРјРёСЂСѓРµРј С„РёРЅР°Р»СЊРЅСѓСЋ СЃС‚СЂРѕРєСѓ РїРѕ С€Р°Р±Р»РѕРЅСѓ
        return self.PAYMENT_BALANCE_TEMPLATE.format(service_name=self.PAYMENT_SERVICE_NAME, description=description)

    def get_subscription_payment_description(self, period_days: int, amount_kopeks: int) -> str:
        return self.PAYMENT_SUBSCRIPTION_TEMPLATE.format(
            service_name=self.PAYMENT_SERVICE_NAME,
            description=f'{self.PAYMENT_SUBSCRIPTION_DESCRIPTION} РЅР° {period_days} РґРЅРµР№',
        )

    def get_custom_payment_description(self, description: str) -> str:
        return self.PAYMENT_BALANCE_TEMPLATE.format(service_name=self.PAYMENT_SERVICE_NAME, description=description)

    def get_stars_rate(self) -> float:
        return self.TELEGRAM_STARS_RATE_RUB

    def get_telegram_stars_display_name(self) -> str:
        name = (self.TELEGRAM_STARS_DISPLAY_NAME or '').strip()
        return name or 'Telegram Stars'

    def stars_to_rubles(self, stars: int) -> float:
        return stars * self.get_stars_rate()

    def rubles_to_stars(self, rubles: float) -> int:
        rate = self.get_stars_rate()
        if rate <= 0:
            raise ValueError('Stars rate must be positive')
        return max(1, round(rubles / rate))

    def get_admin_notifications_chat_id(self) -> int | None:
        if not self.ADMIN_NOTIFICATIONS_CHAT_ID:
            return None

        try:
            return int(self.ADMIN_NOTIFICATIONS_CHAT_ID)
        except (ValueError, TypeError):
            return None

    def is_admin_notifications_enabled(self) -> bool:
        return self.ADMIN_NOTIFICATIONS_ENABLED and self.get_admin_notifications_chat_id() is not None

    def get_backup_send_chat_id(self) -> int | None:
        if not self.BACKUP_SEND_CHAT_ID:
            return None

        try:
            return int(self.BACKUP_SEND_CHAT_ID)
        except (ValueError, TypeError):
            return None

    def is_backup_send_enabled(self) -> bool:
        return self.BACKUP_SEND_ENABLED and self.get_backup_send_chat_id() is not None

    def get_backup_archive_password(self) -> str | None:
        password = (self.BACKUP_ARCHIVE_PASSWORD or '').strip()
        return password or None

    # === Log Rotation Methods ===

    def is_log_rotation_enabled(self) -> bool:
        """РџСЂРѕРІРµСЂРёС‚СЊ, РІРєР»СЋС‡РµРЅР° Р»Рё РЅРѕРІР°СЏ СЃРёСЃС‚РµРјР° СЂРѕС‚Р°С†РёРё Р»РѕРіРѕРІ."""
        return self.LOG_ROTATION_ENABLED

    def get_log_rotation_chat_id(self) -> int | None:
        """РџРѕР»СѓС‡РёС‚СЊ ID РєР°РЅР°Р»Р° РґР»СЏ РѕС‚РїСЂР°РІРєРё Р»РѕРіРѕРІ.

        Р•СЃР»Рё LOG_ROTATION_CHAT_ID РЅРµ Р·Р°РґР°РЅ, РёСЃРїРѕР»СЊР·СѓРµС‚ BACKUP_SEND_CHAT_ID.
        """
        chat_id = self.LOG_ROTATION_CHAT_ID or self.BACKUP_SEND_CHAT_ID
        if not chat_id:
            return None

        try:
            return int(chat_id)
        except (ValueError, TypeError):
            return None

    def get_log_rotation_topic_id(self) -> int | None:
        """РџРѕР»СѓС‡РёС‚СЊ ID С‚РѕРїРёРєР° РґР»СЏ РѕС‚РїСЂР°РІРєРё Р»РѕРіРѕРІ.

        Р•СЃР»Рё LOG_ROTATION_TOPIC_ID РЅРµ Р·Р°РґР°РЅ, РёСЃРїРѕР»СЊР·СѓРµС‚ BACKUP_SEND_TOPIC_ID.
        """
        topic_id = self.LOG_ROTATION_TOPIC_ID
        if topic_id is not None:
            return topic_id
        return self.BACKUP_SEND_TOPIC_ID

    def get_referral_settings(self) -> dict:
        return {
            'program_enabled': self.is_referral_program_enabled(),
            'minimum_topup_kopeks': self.REFERRAL_MINIMUM_TOPUP_KOPEKS,
            'first_topup_bonus_kopeks': self.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS,
            'inviter_bonus_kopeks': self.REFERRAL_INVITER_BONUS_KOPEKS,
            'commission_percent': self.REFERRAL_COMMISSION_PERCENT,
            'first_payment_commission_percent': self.REFERRAL_FIRST_PAYMENT_COMMISSION_PERCENT,
            'recurring_commission_tiers': self.REFERRAL_RECURRING_COMMISSION_TIERS,
            'notifications_enabled': self.REFERRAL_NOTIFICATIONS_ENABLED,
            'withdrawal_enabled': self.REFERRAL_WITHDRAWAL_ENABLED,
            'withdrawal_min_amount_kopeks': self.REFERRAL_WITHDRAWAL_MIN_AMOUNT_KOPEKS,
            'withdrawal_cooldown_days': self.REFERRAL_WITHDRAWAL_COOLDOWN_DAYS,
        }

    def is_referral_withdrawal_enabled(self) -> bool:
        """РџСЂРѕРІРµСЂСЏРµС‚, РІРєР»СЋС‡РµРЅР° Р»Рё С„СѓРЅРєС†РёСЏ РІС‹РІРѕРґР° СЂРµС„РµСЂР°Р»СЊРЅРѕРіРѕ Р±Р°Р»Р°РЅСЃР°."""
        return self.is_referral_program_enabled() and self.REFERRAL_WITHDRAWAL_ENABLED

    def is_referral_program_enabled(self) -> bool:
        return bool(self.REFERRAL_PROGRAM_ENABLED)

    def is_referral_notifications_enabled(self) -> bool:
        return self.REFERRAL_NOTIFICATIONS_ENABLED

    def get_traffic_packages(self) -> list[dict]:
        try:
            packages = []
            config_str = self.TRAFFIC_PACKAGES_CONFIG.strip()

            if not config_str:
                return self._get_fallback_traffic_packages()

            for package_config in config_str.split(','):
                package_config = package_config.strip()
                if not package_config:
                    continue

                parts = package_config.split(':')
                if len(parts) != 3:
                    continue

                try:
                    gb = int(parts[0])
                    price = int(parts[1])
                    enabled = parts[2].lower() == 'true'

                    packages.append({'gb': gb, 'price': price, 'enabled': enabled})
                except ValueError:
                    continue

            return packages or self._get_fallback_traffic_packages()

        except Exception as e:
            logger.warning('ERROR PARSING CONFIG', error=e)
            return self._get_fallback_traffic_packages()

    def is_version_check_enabled(self) -> bool:
        return self.VERSION_CHECK_ENABLED

    def get_version_check_repo(self) -> str:
        return self.VERSION_CHECK_REPO

    def get_version_check_interval(self) -> int:
        return self.VERSION_CHECK_INTERVAL_HOURS

    def _get_fallback_traffic_packages(self) -> list[dict]:
        try:
            if self.TRAFFIC_PACKAGES_CONFIG.strip():
                packages = []
                for package_config in self.TRAFFIC_PACKAGES_CONFIG.split(','):
                    package_config = package_config.strip()
                    if not package_config:
                        continue

                    parts = package_config.split(':')
                    if len(parts) != 3:
                        continue

                    try:
                        gb = int(parts[0])
                        price = int(parts[1])
                        enabled = parts[2].lower() == 'true'

                        packages.append({'gb': gb, 'price': price, 'enabled': enabled})
                    except ValueError:
                        continue

                if packages:
                    return packages
        except Exception:
            pass

        return [
            {'gb': 5, 'price': self.PRICE_TRAFFIC_5GB, 'enabled': True},
            {'gb': 10, 'price': self.PRICE_TRAFFIC_10GB, 'enabled': True},
            {'gb': 25, 'price': self.PRICE_TRAFFIC_25GB, 'enabled': True},
            {'gb': 50, 'price': self.PRICE_TRAFFIC_50GB, 'enabled': True},
            {'gb': 100, 'price': self.PRICE_TRAFFIC_100GB, 'enabled': True},
            {'gb': 250, 'price': self.PRICE_TRAFFIC_250GB, 'enabled': True},
            {'gb': 500, 'price': self.PRICE_TRAFFIC_500GB, 'enabled': True},
            {'gb': 1000, 'price': self.PRICE_TRAFFIC_1000GB, 'enabled': True},
            {'gb': 0, 'price': self.PRICE_TRAFFIC_UNLIMITED, 'enabled': True},
        ]

    def get_traffic_price(self, gb: int | None) -> int:
        packages = self.get_traffic_packages()
        enabled_packages = [pkg for pkg in packages if pkg['enabled']]

        if not enabled_packages:
            return 0

        if gb is None:
            gb = 0

        for package in enabled_packages:
            if package['gb'] == gb:
                return package['price']

        unlimited_package = next((pkg for pkg in enabled_packages if pkg['gb'] == 0), None)

        if gb <= 0:
            return unlimited_package['price'] if unlimited_package else 0

        finite_packages = [pkg for pkg in enabled_packages if pkg['gb'] > 0]

        if not finite_packages:
            return unlimited_package['price'] if unlimited_package else 0

        max_package = max(finite_packages, key=lambda x: x['gb'])

        if gb >= max_package['gb']:
            return unlimited_package['price'] if unlimited_package else max_package['price']

        suitable_packages = [pkg for pkg in finite_packages if pkg['gb'] >= gb]

        if suitable_packages:
            nearest_package = min(suitable_packages, key=lambda x: x['gb'])
            return nearest_package['price']

        return unlimited_package['price'] if unlimited_package else 0

    def _clean_support_contact(self) -> str:
        return (self.SUPPORT_USERNAME or '').strip()

    def get_support_contact_url(self) -> str | None:
        contact = self._clean_support_contact()

        if not contact:
            return None

        if contact.startswith(('http://', 'https://', 'tg://')):
            return contact

        contact_without_prefix = contact.lstrip('@')

        if contact_without_prefix.startswith(('t.me/', 'telegram.me/', 'telegram.dog/')):
            return f'https://{contact_without_prefix}'

        if contact.startswith(('t.me/', 'telegram.me/', 'telegram.dog/')):
            return f'https://{contact}'

        if '.' in contact_without_prefix:
            return f'https://{contact_without_prefix}'

        if contact_without_prefix:
            return f'https://t.me/{contact_without_prefix}'

        return None

    def get_support_contact_display(self) -> str:
        contact = self._clean_support_contact()

        if not contact:
            return ''

        if contact.startswith('@'):
            return contact

        if contact.startswith(('http://', 'https://', 'tg://')):
            return contact

        if contact.startswith(('t.me/', 'telegram.me/', 'telegram.dog/')):
            url = self.get_support_contact_url()
            return url or contact

        contact_without_prefix = contact.lstrip('@')

        if '.' in contact_without_prefix:
            url = self.get_support_contact_url()
            return url or contact

        if re.fullmatch(r'[A-Za-z0-9_]{3,}', contact_without_prefix):
            return f'@{contact_without_prefix}'

        return contact

    def get_support_contact_display_html(self) -> str:
        return html.escape(self.get_support_contact_display())

    def get_server_status_mode(self) -> str:
        return self.SERVER_STATUS_MODE

    def is_server_status_enabled(self) -> bool:
        return self.get_server_status_mode() != 'disabled'

    def get_server_status_external_url(self) -> str | None:
        url = (self.SERVER_STATUS_EXTERNAL_URL or '').strip()
        return url or None

    def get_server_status_metrics_url(self) -> str | None:
        url = (self.SERVER_STATUS_METRICS_URL or '').strip()
        return url or None

    def get_server_status_metrics_auth(self) -> tuple[str, str] | None:
        username = (self.SERVER_STATUS_METRICS_USERNAME or '').strip()
        password_raw = self.SERVER_STATUS_METRICS_PASSWORD

        if not username:
            return None

        password = '' if password_raw is None else str(password_raw)
        return username, password

    def get_server_status_items_per_page(self) -> int:
        return max(1, self.SERVER_STATUS_ITEMS_PER_PAGE)

    def get_server_status_request_timeout(self) -> int:
        return max(1, self.SERVER_STATUS_REQUEST_TIMEOUT)

    def is_web_api_enabled(self) -> bool:
        return bool(self.WEB_API_ENABLED)

    def get_web_api_allowed_origins(self) -> list[str]:
        raw = (self.WEB_API_ALLOWED_ORIGINS or '').split(',')
        origins = [origin.strip() for origin in raw if origin.strip()]
        return origins or ['*']

    def get_web_api_docs_config(self) -> dict[str, str | None]:
        if self.WEB_API_DOCS_ENABLED:
            return {
                'docs_url': '/docs',
                'redoc_url': '/redoc',
                'openapi_url': '/openapi.json',
            }

        return {'docs_url': None, 'redoc_url': None, 'openapi_url': None}

    def get_support_system_mode(self) -> str:
        mode = (self.SUPPORT_SYSTEM_MODE or 'both').strip().lower()
        return mode if mode in {'tickets', 'contact', 'both'} else 'both'

    def is_support_tickets_enabled(self) -> bool:
        return self.get_support_system_mode() in {'tickets', 'both'}

    def is_support_contact_enabled(self) -> bool:
        return self.get_support_system_mode() in {'contact', 'both'}

    # MiniApp tickets settings
    def is_miniapp_tickets_enabled(self) -> bool:
        """Check if tickets are enabled in miniapp."""
        return bool(self.MINIAPP_TICKETS_ENABLED)

    def get_miniapp_support_type(self) -> str:
        """Get miniapp support type: tickets, profile, or url."""
        support_type = (self.MINIAPP_SUPPORT_TYPE or 'tickets').strip().lower()
        return support_type if support_type in {'tickets', 'profile', 'url'} else 'tickets'

    def get_miniapp_support_url(self) -> str:
        """Get custom support URL for miniapp (when type is 'url')."""
        return (self.MINIAPP_SUPPORT_URL or '').strip()

    def get_bot_run_mode(self) -> str:
        mode = (self.BOT_RUN_MODE or 'polling').strip().lower()
        if mode not in {'polling', 'webhook'}:
            return 'polling'
        return mode

    def get_telegram_webhook_path(self) -> str:
        raw_path = (self.WEBHOOK_PATH or '/webhook').strip()
        if not raw_path:
            raw_path = '/webhook'
        if not raw_path.startswith('/'):
            raw_path = '/' + raw_path
        return raw_path

    def get_webhook_queue_maxsize(self) -> int:
        try:
            size = int(self.WEBHOOK_MAX_QUEUE_SIZE)
        except (TypeError, ValueError):
            size = 1024
        return max(1, size)

    def get_webhook_worker_count(self) -> int:
        try:
            workers = int(self.WEBHOOK_WORKERS)
        except (TypeError, ValueError):
            workers = 1
        return max(1, workers)

    def get_webhook_enqueue_timeout(self) -> float:
        try:
            timeout = float(self.WEBHOOK_ENQUEUE_TIMEOUT)
        except (TypeError, ValueError):
            timeout = 0.0
        return max(0.0, timeout)

    def get_webhook_shutdown_timeout(self) -> float:
        try:
            timeout = float(self.WEBHOOK_WORKER_SHUTDOWN_TIMEOUT)
        except (TypeError, ValueError):
            timeout = 30.0
        return max(1.0, timeout)

    def get_telegram_webhook_url(self) -> str | None:
        base_url = (self.WEBHOOK_URL or '').strip()
        if not base_url:
            return None
        path = self.get_telegram_webhook_path()
        return f'{base_url.rstrip("/")}{path}'

    def get_miniapp_static_path(self) -> Path:
        raw_path = (self.MINIAPP_STATIC_PATH or 'miniapp').strip()
        if not raw_path:
            raw_path = 'miniapp'
        return Path(raw_path)

    def get_media_upload_path(self) -> Path:
        return Path(self.MEDIA_UPLOAD_DIR)

    # Cabinet methods
    def is_cabinet_enabled(self) -> bool:
        return bool(self.CABINET_ENABLED)

    def get_cabinet_jwt_secret(self) -> str:
        if self.CABINET_JWT_SECRET:
            return self.CABINET_JWT_SECRET
        import warnings

        warnings.warn(
            'CABINET_JWT_SECRET is not set, falling back to BOT_TOKEN. '
            'Set CABINET_JWT_SECRET to a unique secret in production: '
            'python -c "import secrets; print(secrets.token_urlsafe(64))"',
            UserWarning,
            stacklevel=2,
        )
        return self.BOT_TOKEN

    def collect_insecure_default_warnings(self) -> list[str]:
        """Return warnings about insecure default/secret configuration.

        Surfaced once at startup (via the structured logger) so operators notice when the
        bot runs with shipped defaults that must be changed before production.
        """
        messages: list[str] = []

        if self.POSTGRES_PASSWORD == 'secure_password_123' and 'postgresql' in self.get_database_url():
            messages.append(
                'POSTGRES_PASSWORD is the shipped default ("secure_password_123"). '
                'Set a unique strong password before exposing this deployment.'
            )

        if self.is_cabinet_enabled() and not self.CABINET_JWT_SECRET:
            messages.append(
                'CABINET_JWT_SECRET is not set вЂ” cabinet JWTs are signed with BOT_TOKEN, which is '
                'widely exposed (Telegram API, payment-provider configs). A BOT_TOKEN leak would let '
                'anyone forge cabinet sessions. Set CABINET_JWT_SECRET to a unique value: '
                'python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )

        return messages

    def get_cabinet_access_token_expire_minutes(self) -> int:
        return max(1, self.CABINET_ACCESS_TOKEN_EXPIRE_MINUTES)

    def get_cabinet_refresh_token_expire_days(self) -> int:
        return max(1, self.CABINET_REFRESH_TOKEN_EXPIRE_DAYS)

    def get_cabinet_allowed_origins(self) -> list[str]:
        if not self.CABINET_ALLOWED_ORIGINS:
            return []
        return [o.strip() for o in self.CABINET_ALLOWED_ORIGINS.split(',') if o.strip()]

    def is_cabinet_email_verification_enabled(self) -> bool:
        return bool(self.CABINET_EMAIL_VERIFICATION_ENABLED)

    def get_cabinet_email_verification_expire_hours(self) -> int:
        return max(1, self.CABINET_EMAIL_VERIFICATION_EXPIRE_HOURS)

    def get_cabinet_password_reset_expire_hours(self) -> int:
        return max(1, self.CABINET_PASSWORD_RESET_EXPIRE_HOURS)

    def get_cabinet_email_change_code_expire_minutes(self) -> int:
        return max(1, self.CABINET_EMAIL_CHANGE_CODE_EXPIRE_MINUTES)

    def is_cabinet_email_auth_enabled(self) -> bool:
        return bool(self.CABINET_EMAIL_AUTH_ENABLED)

    def get_cabinet_trusted_proxies(self) -> set[str]:
        """Parse CABINET_TRUSTED_PROXIES into a set of IP strings/CIDRs."""
        if not self.CABINET_TRUSTED_PROXIES:
            return set()
        return {p.strip() for p in self.CABINET_TRUSTED_PROXIES.split(',') if p.strip()}

    def is_smtp_configured(self) -> bool:
        # For servers without AUTH, only host and from_email are required
        has_from = bool(self.SMTP_FROM_EMAIL or self.SMTP_USER)
        return bool(self.SMTP_HOST and has_from)

    def get_smtp_from_email(self) -> str | None:
        if self.SMTP_FROM_EMAIL:
            return self.SMTP_FROM_EMAIL
        return self.SMTP_USER

    # OAuth helpers
    def get_oauth_providers_config(self) -> dict[str, dict[str, str | bool]]:
        """Return config for all OAuth providers (enabled or not)."""
        return {
            'google': {
                'client_id': self.OAUTH_GOOGLE_CLIENT_ID,
                'client_secret': self.OAUTH_GOOGLE_CLIENT_SECRET,
                'enabled': self.OAUTH_GOOGLE_ENABLED,
                'display_name': 'Google',
            },
            'yandex': {
                'client_id': self.OAUTH_YANDEX_CLIENT_ID,
                'client_secret': self.OAUTH_YANDEX_CLIENT_SECRET,
                'enabled': self.OAUTH_YANDEX_ENABLED,
                'display_name': 'Yandex',
            },
            'discord': {
                'client_id': self.OAUTH_DISCORD_CLIENT_ID,
                'client_secret': self.OAUTH_DISCORD_CLIENT_SECRET,
                'enabled': self.OAUTH_DISCORD_ENABLED,
                'display_name': 'Discord',
            },
            'vk': {
                'client_id': self.OAUTH_VK_CLIENT_ID,
                'client_secret': self.OAUTH_VK_CLIENT_SECRET,
                'enabled': self.OAUTH_VK_ENABLED,
                'display_name': 'VK',
            },
        }

    def get_enabled_oauth_provider_names(self) -> list[str]:
        """Return list of enabled OAuth provider names."""
        return [name for name, cfg in self.get_oauth_providers_config().items() if cfg['enabled']]

    # Ban System helpers
    def is_ban_system_enabled(self) -> bool:
        return bool(self.BAN_SYSTEM_ENABLED)

    def is_ban_system_configured(self) -> bool:
        return bool(self.BAN_SYSTEM_API_URL and self.BAN_SYSTEM_API_TOKEN)

    def get_ban_system_api_url(self) -> str | None:
        if self.BAN_SYSTEM_API_URL:
            return self.BAN_SYSTEM_API_URL.rstrip('/')
        return None

    def get_ban_system_api_token(self) -> str | None:
        return self.BAN_SYSTEM_API_TOKEN

    def get_ban_system_request_timeout(self) -> int:
        return max(1, self.BAN_SYSTEM_REQUEST_TIMEOUT)

    model_config = {'env_file': '.env', 'env_file_encoding': 'utf-8', 'extra': 'ignore'}

    @field_validator('TIMEZONE')
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as exc:  # pragma: no cover - defensive validation
            raise ValueError(f'РќРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ РёРґРµРЅС‚РёС„РёРєР°С‚РѕСЂ С‡Р°СЃРѕРІРѕРіРѕ РїРѕСЏСЃР°: {value}') from exc
        return value


settings = Settings()
ENV_OVERRIDE_KEYS = set(settings.model_fields_set)

_PERIOD_PRICE_FIELDS: dict[int, str] = {
    14: 'PRICE_14_DAYS',
    30: 'PRICE_30_DAYS',
    60: 'PRICE_60_DAYS',
    90: 'PRICE_90_DAYS',
    180: 'PRICE_180_DAYS',
    360: 'PRICE_360_DAYS',
}

# РҐСЂР°РЅРёР»РёС‰Рµ РїРµСЂРёРѕРґРѕРІ/С†РµРЅ РёР· Р‘Р” (РїСЂРёРѕСЂРёС‚РµС‚ РЅР°Рґ .env)
_DB_PERIOD_PRICES: dict[int, int] | None = None


def set_period_prices_from_db(period_prices: dict[int, int]) -> None:
    """
    РЈСЃС‚Р°РЅР°РІР»РёРІР°РµС‚ РїРµСЂРёРѕРґС‹/С†РµРЅС‹ РёР· Р‘Р”.
    Р’С‹Р·С‹РІР°РµС‚СЃСЏ РїРѕСЃР»Рµ СЃРёРЅС…СЂРѕРЅРёР·Р°С†РёРё С‚Р°СЂРёС„РѕРІ РїСЂРё Р·Р°РїСѓСЃРєРµ Р±РѕС‚Р°.
    """
    global _DB_PERIOD_PRICES
    _DB_PERIOD_PRICES = period_prices.copy() if period_prices else None
    refresh_period_prices()


def get_db_period_prices() -> dict[int, int] | None:
    """Р’РѕР·РІСЂР°С‰Р°РµС‚ РїРµСЂРёРѕРґС‹/С†РµРЅС‹ РёР· Р‘Р” РµСЃР»Рё РѕРЅРё Р·Р°РіСЂСѓР¶РµРЅС‹."""
    return _DB_PERIOD_PRICES


def clear_db_period_prices() -> None:
    """РћС‡РёС‰Р°РµС‚ РєРµС€ С†РµРЅ РёР· С‚Р°СЂРёС„РѕРІ (РїСЂРё РїРµСЂРµРєР»СЋС‡РµРЅРёРё РІ classic mode)."""
    global _DB_PERIOD_PRICES
    _DB_PERIOD_PRICES = None


def refresh_period_prices() -> None:
    """
    Rebuild cached period price mapping.
    Р’ СЂРµР¶РёРјРµ tariffs: РїСЂРёРѕСЂРёС‚РµС‚ Сѓ _DB_PERIOD_PRICES (РёР· С‚Р°Р±Р»РёС†С‹ Tariff).
    Р’ СЂРµР¶РёРјРµ classic: Р’РЎР•Р“Р”Рђ РёСЃРїРѕР»СЊР·СѓСЋС‚СЃСЏ settings.PRICE_*_DAYS.
    """
    PERIOD_PRICES.clear()

    if _DB_PERIOD_PRICES and settings.is_tariffs_mode():
        # РСЃРїРѕР»СЊР·СѓРµРј С†РµРЅС‹ РёР· Р‘Р” С‚Р°СЂРёС„РѕРІ (С‚РѕР»СЊРєРѕ РІ СЂРµР¶РёРјРµ tariffs)
        PERIOD_PRICES.update(_DB_PERIOD_PRICES)
    else:
        # Classic mode РёР»Рё РЅРµС‚ С†РµРЅ РІ Р‘Р” вЂ” Р±РµСЂС‘Рј РёР· settings
        PERIOD_PRICES.update(
            {days: getattr(settings, field_name, 0) for days, field_name in _PERIOD_PRICE_FIELDS.items()}
        )


PERIOD_PRICES: dict[int, int] = {}
refresh_period_prices()


def _build_classic_period_prices() -> dict[int, int]:
    """Build classic-mode period prices directly from PRICE_*_DAYS settings.

    Unlike PERIOD_PRICES (which may use DB tariff prices in tariffs mode),
    this always reflects the env/settings values вЂ” the canonical prices for
    classic (non-tariff) subscriptions.
    """
    return {days: getattr(settings, field_name, 0) for days, field_name in _PERIOD_PRICE_FIELDS.items()}


CLASSIC_PERIOD_PRICES: dict[int, int] = _build_classic_period_prices()


def refresh_classic_period_prices() -> None:
    """Rebuild CLASSIC_PERIOD_PRICES from current settings."""
    CLASSIC_PERIOD_PRICES.clear()
    CLASSIC_PERIOD_PRICES.update(_build_classic_period_prices())


def get_traffic_prices() -> dict[int, int]:
    packages = settings.get_traffic_packages()
    return {package['gb']: package['price'] for package in packages}


TRAFFIC_PRICES = get_traffic_prices()


def refresh_traffic_prices():
    global TRAFFIC_PRICES
    TRAFFIC_PRICES = get_traffic_prices()


refresh_traffic_prices()

settings._original_database_url = settings.DATABASE_URL
settings.DATABASE_URL = settings.get_database_url()
