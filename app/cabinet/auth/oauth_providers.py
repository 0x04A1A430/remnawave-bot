"""OAuth 2.0 provider implementations for cabinet authentication."""

import logging
import secrets
from abc import ABC, abstractmethod
from typing import Any, TypedDict

import httpx
from pydantic import BaseModel

from app.config import settings
from app.utils.cache import cache, cache_key


logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = 600  # 10 minutes


# --- Typed dicts for provider API responses ---


class OAuthProviderConfig(TypedDict):
    client_id: str
    client_secret: str
    enabled: bool
    display_name: str


class OAuthTokenResponse(TypedDict, total=False):
    access_token: str
    token_type: str
    expires_in: int
    refresh_token: str
    scope: str
    # VK-specific: email and user_id come in token response
    email: str
    user_id: int


class GoogleUserInfoResponse(TypedDict, total=False):
    sub: str
    email: str
    email_verified: bool
    given_name: str
    family_name: str
    picture: str
    name: str


class YandexUserInfoResponse(TypedDict, total=False):
    id: str
    login: str
    default_email: str
    emails: list[str]
    first_name: str
    last_name: str
    default_avatar_id: str


class DiscordUserInfoResponse(TypedDict, total=False):
    id: str
    username: str
    global_name: str
    email: str
    verified: bool
    avatar: str


class VKUserInfoItem(TypedDict, total=False):
    id: int
    first_name: str
    last_name: str
    photo_200: str


class VKUserInfoResponse(TypedDict, total=False):
    response: list[VKUserInfoItem]


# --- Models ---


class OAuthUserInfo(BaseModel):
    """Normalized user info from OAuth provider."""

    provider: str
    provider_id: str
    email: str | None = None
    email_verified: bool = False
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    avatar_url: str | None = None


# --- CSRF state management (Redis) ---


async def generate_oauth_state(provider: str) -> str:
    """Generate a CSRF state token for OAuth flow. Stored in Redis with TTL."""
    state = secrets.token_urlsafe(32)
    await cache.set(cache_key('oauth_state', state), provider, expire=STATE_TTL_SECONDS)
    return state


async def validate_oauth_state(state: str, provider: str) -> bool:
    """Validate and consume a CSRF state token from Redis."""
    key = cache_key('oauth_state', state)
    stored_provider: str | None = await cache.get(key)
    if stored_provider is None:
        return False
    await cache.delete(key)
    if stored_provider != provider:
        return False
    return True


# --- Provider implementations ---


class OAuthProvider(ABC):
    """Base class for OAuth 2.0 providers."""

    name: str
    display_name: str

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    @abstractmethod
    def get_authorization_url(self, state: str) -> str:
        """Build the authorization URL for the provider."""

    @abstractmethod
    async def exchange_code(self, code: str) -> OAuthTokenResponse:
        """Exchange authorization code for tokens."""

    @abstractmethod
    async def get_user_info(self, token_data: OAuthTokenResponse) -> OAuthUserInfo:
        """Fetch user info from the provider."""


class GoogleProvider(OAuthProvider):
    name = 'google'
    display_name = 'Google'

    AUTHORIZE_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
    TOKEN_URL = 'https://oauth2.googleapis.com/token'
    USERINFO_URL = 'https://www.googleapis.com/oauth2/v3/userinfo'

    def get_authorization_url(self, state: str) -> str:
        params: dict[str, str] = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'openid email profile',
            'state': state,
            'access_type': 'offline',
            'prompt': 'select_account',
        }
        request = httpx.Request('GET', self.AUTHORIZE_URL, params=params)
        return str(request.url)

    async def exchange_code(self, code: str) -> OAuthTokenResponse:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                self.TOKEN_URL,
                json={
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'code': code,
                    'grant_type': 'authorization_code',
                    'redirect_uri': self.redirect_uri,
                },
            )
            response.raise_for_status()
            data: OAuthTokenResponse = response.json()
            return data

    async def get_user_info(self, token_data: OAuthTokenResponse) -> OAuthUserInfo:
        access_token = token_data['access_token']
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                self.USERINFO_URL,
                headers={'Authorization': f'Bearer {access_token}'},
            )
            response.raise_for_status()
            data: GoogleUserInfoResponse = response.json()

        return OAuthUserInfo(
            provider='google',
            provider_id=str(data['sub']),
            email=data.get('email'),
            email_verified=data.get('email_verified', False),
            first_name=data.get('given_name'),
            last_name=data.get('family_name'),
            avatar_url=data.get('picture'),
        )


class YandexProvider(OAuthProvider):
    name = 'yandex'
    display_name = 'Yandex'

    AUTHORIZE_URL = 'https://oauth.yandex.com/authorize'
    TOKEN_URL = 'https://oauth.yandex.com/token'
    USERINFO_URL = 'https://login.yandex.ru/info'

    def get_authorization_url(self, state: str) -> str:
        params: dict[str, str] = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'login:info login:email',
            'state': state,
            'force_confirm': 'yes',
        }
        request = httpx.Request('GET', self.AUTHORIZE_URL, params=params)
        return str(request.url)

    async def exchange_code(self, code: str) -> OAuthTokenResponse:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'code': code,
                    'grant_type': 'authorization_code',
                },
            )
            response.raise_for_status()
            data: OAuthTokenResponse = response.json()
            return data

    async def get_user_info(self, token_data: OAuthTokenResponse) -> OAuthUserInfo:
        access_token = token_data['access_token']
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                self.USERINFO_URL,
                params={'format': 'json'},
                headers={'Authorization': f'OAuth {access_token}'},
            )
            response.raise_for_status()
            data: YandexUserInfoResponse = response.json()

        default_email = data.get('default_email')
        emails = data.get('emails', [])
        email = default_email or (emails[0] if emails else None)

        return OAuthUserInfo(
            provider='yandex',
            provider_id=str(data['id']),
            email=email,
            email_verified=bool(email),
            first_name=data.get('first_name'),
            last_name=data.get('last_name'),
            username=data.get('login'),
            avatar_url=(
                f'https://avatars.yandex.net/get-yapic/{data["default_avatar_id"]}/islands-200'
                if data.get('default_avatar_id')
                else None
            ),
        )


class DiscordProvider(OAuthProvider):
    name = 'discord'
    display_name = 'Discord'

    AUTHORIZE_URL = 'https://discord.com/api/oauth2/authorize'
    TOKEN_URL = 'https://discord.com/api/oauth2/token'
    USERINFO_URL = 'https://discord.com/api/v10/users/@me'

    def get_authorization_url(self, state: str) -> str:
        params: dict[str, str] = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'identify email',
            'state': state,
            'prompt': 'consent',
        }
        request = httpx.Request('GET', self.AUTHORIZE_URL, params=params)
        return str(request.url)

    async def exchange_code(self, code: str) -> OAuthTokenResponse:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                self.TOKEN_URL,
                data={
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'code': code,
                    'grant_type': 'authorization_code',
                    'redirect_uri': self.redirect_uri,
                },
            )
            response.raise_for_status()
            data: OAuthTokenResponse = response.json()
            return data

    async def get_user_info(self, token_data: OAuthTokenResponse) -> OAuthUserInfo:
        access_token = token_data['access_token']
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                self.USERINFO_URL,
                headers={'Authorization': f'Bearer {access_token}'},
            )
            response.raise_for_status()
            data: DiscordUserInfoResponse = response.json()

        avatar_url: str | None = None
        if data.get('avatar'):
            avatar_url = f'https://cdn.discordapp.com/avatars/{data["id"]}/{data["avatar"]}.png'

        return OAuthUserInfo(
            provider='discord',
            provider_id=str(data['id']),
            email=data.get('email'),
            email_verified=data.get('verified', False),
            first_name=data.get('global_name') or data.get('username'),
            username=data.get('username'),
            avatar_url=avatar_url,
        )


class VKProvider(OAuthProvider):
    name = 'vk'
    display_name = 'VK'

    AUTHORIZE_URL = 'https://oauth.vk.com/authorize'
    TOKEN_URL = 'https://oauth.vk.com/access_token'
    USERINFO_URL = 'https://api.vk.com/method/users.get'
    API_VERSION = '5.131'

    def get_authorization_url(self, state: str) -> str:
        params: dict[str, str] = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'email',
            'state': state,
            'v': self.API_VERSION,
        }
        request = httpx.Request('GET', self.AUTHORIZE_URL, params=params)
        return str(request.url)

    async def exchange_code(self, code: str) -> OAuthTokenResponse:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                self.TOKEN_URL,
                params={
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'code': code,
                    'redirect_uri': self.redirect_uri,
                },
            )
            response.raise_for_status()
            data: OAuthTokenResponse = response.json()
            return data

    async def get_user_info(self, token_data: OAuthTokenResponse) -> OAuthUserInfo:
        access_token = token_data['access_token']
        user_id: int | None = token_data.get('user_id')
        # VK returns email in token response, not in userinfo
        email: str | None = token_data.get('email')

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                self.USERINFO_URL,
                params={
                    'access_token': access_token,
                    'fields': 'photo_200',
                    'v': self.API_VERSION,
                },
            )
            response.raise_for_status()
            data: VKUserInfoResponse = response.json()

        users: list[Any] = data.get('response', [])
        user_data: VKUserInfoItem = users[0] if users else {}  # type: ignore[assignment]

        return OAuthUserInfo(
            provider='vk',
            provider_id=str(user_id or user_data.get('id', '')),
            email=email,
            email_verified=bool(email),
            first_name=user_data.get('first_name'),
            last_name=user_data.get('last_name'),
            avatar_url=user_data.get('photo_200'),
        )


# --- Provider factory ---

_PROVIDERS: dict[str, type[OAuthProvider]] = {
    'google': GoogleProvider,
    'yandex': YandexProvider,
    'discord': DiscordProvider,
    'vk': VKProvider,
}


def get_provider(name: str) -> OAuthProvider | None:
    """Get an OAuth provider instance if enabled.

    Returns None if the provider is not enabled or not found.
    """
    providers_config: dict[str, OAuthProviderConfig] = settings.get_oauth_providers_config()
    config = providers_config.get(name)
    if not config or not config['enabled']:
        return None

    provider_class = _PROVIDERS.get(name)
    if not provider_class:
        return None

    redirect_uri = f'{settings.CABINET_URL}/auth/oauth/callback'

    return provider_class(
        client_id=config['client_id'],
        client_secret=config['client_secret'],
        redirect_uri=redirect_uri,
    )
