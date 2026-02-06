"""OAuth 2.0 provider implementations for cabinet authentication."""

import logging
import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.config import settings


logger = logging.getLogger(__name__)

# In-memory CSRF state store with TTL
_oauth_states: dict[str, tuple[str, float]] = {}
STATE_TTL_SECONDS = 600  # 10 minutes


@dataclass
class OAuthUserInfo:
    """Normalized user info from OAuth provider."""

    provider: str
    provider_id: str
    email: str | None = None
    email_verified: bool = False
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    avatar_url: str | None = None


def generate_oauth_state(provider: str) -> str:
    """Generate a CSRF state token for OAuth flow."""
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = (provider, time.time())
    _cleanup_expired_states()
    return state


def validate_oauth_state(state: str, provider: str) -> bool:
    """Validate and consume a CSRF state token."""
    entry = _oauth_states.pop(state, None)
    if entry is None:
        return False
    stored_provider, created_at = entry
    if stored_provider != provider:
        return False
    if time.time() - created_at > STATE_TTL_SECONDS:
        return False
    return True


def _cleanup_expired_states() -> None:
    """Remove expired state tokens."""
    now = time.time()
    expired = [k for k, (_, ts) in _oauth_states.items() if now - ts > STATE_TTL_SECONDS]
    for k in expired:
        _oauth_states.pop(k, None)


class OAuthProvider(ABC):
    """Base class for OAuth 2.0 providers."""

    name: str
    display_name: str

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    @abstractmethod
    def get_authorization_url(self, state: str) -> str:
        """Build the authorization URL for the provider."""

    @abstractmethod
    async def exchange_code(self, code: str) -> dict:
        """Exchange authorization code for tokens."""

    @abstractmethod
    async def get_user_info(self, token_data: dict) -> OAuthUserInfo:
        """Fetch user info from the provider."""


class GoogleProvider(OAuthProvider):
    name = 'google'
    display_name = 'Google'

    def get_authorization_url(self, state: str) -> str:
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'openid email profile',
            'state': state,
            'access_type': 'offline',
            'prompt': 'select_account',
        }
        return f'https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}'

    async def exchange_code(self, code: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                'https://oauth2.googleapis.com/token',
                json={
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'code': code,
                    'grant_type': 'authorization_code',
                    'redirect_uri': self.redirect_uri,
                },
            )
            response.raise_for_status()
            return response.json()

    async def get_user_info(self, token_data: dict) -> OAuthUserInfo:
        access_token = token_data['access_token']
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                'https://www.googleapis.com/oauth2/v3/userinfo',
                headers={'Authorization': f'Bearer {access_token}'},
            )
            response.raise_for_status()
            data = response.json()

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

    def get_authorization_url(self, state: str) -> str:
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'login:info login:email',
            'state': state,
            'force_confirm': 'yes',
        }
        return f'https://oauth.yandex.com/authorize?{urlencode(params)}'

    async def exchange_code(self, code: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                'https://oauth.yandex.com/token',
                data={
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'code': code,
                    'grant_type': 'authorization_code',
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            )
            response.raise_for_status()
            return response.json()

    async def get_user_info(self, token_data: dict) -> OAuthUserInfo:
        access_token = token_data['access_token']
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                'https://login.yandex.ru/info?format=json',
                headers={'Authorization': f'OAuth {access_token}'},
            )
            response.raise_for_status()
            data = response.json()

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

    def get_authorization_url(self, state: str) -> str:
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'identify email',
            'state': state,
            'prompt': 'consent',
        }
        return f'https://discord.com/api/oauth2/authorize?{urlencode(params)}'

    async def exchange_code(self, code: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                'https://discord.com/api/oauth2/token',
                data={
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'code': code,
                    'grant_type': 'authorization_code',
                    'redirect_uri': self.redirect_uri,
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            )
            response.raise_for_status()
            return response.json()

    async def get_user_info(self, token_data: dict) -> OAuthUserInfo:
        access_token = token_data['access_token']
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                'https://discord.com/api/v10/users/@me',
                headers={'Authorization': f'Bearer {access_token}'},
            )
            response.raise_for_status()
            data = response.json()

        avatar_url = None
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

    def get_authorization_url(self, state: str) -> str:
        params = {
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'scope': 'email',
            'state': state,
            'v': '5.131',
        }
        return f'https://oauth.vk.com/authorize?{urlencode(params)}'

    async def exchange_code(self, code: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                'https://oauth.vk.com/access_token',
                params={
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                    'code': code,
                    'redirect_uri': self.redirect_uri,
                },
            )
            response.raise_for_status()
            return response.json()

    async def get_user_info(self, token_data: dict) -> OAuthUserInfo:
        access_token = token_data['access_token']
        user_id = token_data.get('user_id')
        # VK returns email in token response, not in userinfo
        email = token_data.get('email')

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                'https://api.vk.com/method/users.get',
                params={
                    'access_token': access_token,
                    'fields': 'photo_200',
                    'v': '5.131',
                },
            )
            response.raise_for_status()
            data = response.json()

        user_data = data.get('response', [{}])[0]

        return OAuthUserInfo(
            provider='vk',
            provider_id=str(user_id or user_data.get('id', '')),
            email=email,
            email_verified=bool(email),
            first_name=user_data.get('first_name'),
            last_name=user_data.get('last_name'),
            avatar_url=user_data.get('photo_200'),
        )


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
    providers_config = settings.get_oauth_providers_config()
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
