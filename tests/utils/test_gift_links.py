"""Contract tests for canonical gift claim links and Telegram share links."""

from __future__ import annotations

import urllib.parse

import pytest

from app.database.crud.landing import generate_purchase_token
from app.utils.gift_links import (
    GIFT_TOKEN_BOT_PREFIX_LENGTH,
    GIFT_TOKEN_MIN_PREFIX_LENGTH,
    TELEGRAM_GIFT_START_PREFIX,
    TELEGRAM_START_PARAM_MAX_LENGTH,
    GiftLinkError,
    InvalidBotUsernameError,
    InvalidCabinetUrlError,
    InvalidClaimLinkError,
    InvalidGiftTokenError,
    InvalidShareTextError,
    MissingBotUsernameError,
    MissingCabinetUrlError,
    build_bot_gift_claim_link,
    build_cabinet_gift_claim_link,
    build_telegram_gift_share_url,
)


class TestBuildBotGiftClaimLink:
    """Tests for build_bot_gift_claim_link."""

    def test_start_param_within_telegram_64_char_limit(self) -> None:
        token = generate_purchase_token()
        assert len(token) == 64

        link = build_bot_gift_claim_link(token, 'test_bot')
        parsed = urllib.parse.urlparse(link)
        assert parsed.scheme == 'https'
        assert parsed.netloc == 't.me'
        assert parsed.path == '/test_bot'

        query = urllib.parse.parse_qs(parsed.query)
        assert 'start' in query
        start_param = query['start'][0]

        # Invariant: start param is at most 64 characters total
        assert len(start_param) <= TELEGRAM_START_PARAM_MAX_LENGTH
        assert len(start_param) == 64

    def test_start_param_prefix_and_token_fragment_security_floor(self) -> None:
        token = generate_purchase_token()
        link = build_bot_gift_claim_link(token, 'test_bot')

        parsed = urllib.parse.urlparse(link)
        start_param = urllib.parse.parse_qs(parsed.query)['start'][0]

        assert start_param.startswith(TELEGRAM_GIFT_START_PREFIX)
        fragment = start_param.removeprefix(TELEGRAM_GIFT_START_PREFIX)

        # Security floor: must contain at least 48 characters of entropy
        assert len(fragment) >= GIFT_TOKEN_MIN_PREFIX_LENGTH
        assert len(fragment) == GIFT_TOKEN_BOT_PREFIX_LENGTH  # 59 characters
        assert len(fragment) == 59
        # The fragment is a strict prefix of the full token
        assert fragment == token[:59]

    @pytest.mark.parametrize(
        ('raw_username', 'expected_netloc_path'),
        [
            ('my_gift_bot', '/my_gift_bot'),
            ('@my_gift_bot', '/my_gift_bot'),
            ('  @my_gift_bot  ', '/my_gift_bot'),
            ('bot123', '/bot123'),
            ('@bot_with_numbers_123', '/bot_with_numbers_123'),
        ],
    )
    def test_username_normalization(self, raw_username: str, expected_netloc_path: str) -> None:
        token = generate_purchase_token()
        link = build_bot_gift_claim_link(token, raw_username)
        parsed = urllib.parse.urlparse(link)
        assert parsed.path == expected_netloc_path

    def test_preserves_urlsafe_token_characters(self) -> None:
        token = 'A' * 30 + '-' + 'B' * 20 + '_' + 'C' * 12
        assert len(token) == 64
        link = build_bot_gift_claim_link(token, 'test_bot')
        assert f'start=GIFT_{token[:59]}' in link

    def test_accepts_minimum_length_token(self) -> None:
        token = 'x' * GIFT_TOKEN_MIN_PREFIX_LENGTH
        link = build_bot_gift_claim_link(token, 'test_bot')
        parsed = urllib.parse.urlparse(link)
        start_param = urllib.parse.parse_qs(parsed.query)['start'][0]
        assert start_param == f'GIFT_{token}'
        assert len(start_param) == 5 + 48

    @pytest.mark.parametrize(
        'missing_username',
        [None, '', '   ', '@', '  @  '],
    )
    def test_rejects_missing_or_blank_bot_username(self, missing_username: str | None) -> None:
        token = generate_purchase_token()
        with pytest.raises(MissingBotUsernameError):
            build_bot_gift_claim_link(token, missing_username)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        'malformed_username',
        ['bot with spaces', 'bot#name', 'bot!@?', 'user$name', 'invalid/path'],
    )
    def test_rejects_malformed_bot_username(self, malformed_username: str) -> None:
        token = generate_purchase_token()
        with pytest.raises(InvalidBotUsernameError):
            build_bot_gift_claim_link(token, malformed_username)

    @pytest.mark.parametrize(
        'too_short_token',
        ['', 'abc', 'X' * 8, 'X' * 12, 'X' * 47],
    )
    def test_rejects_too_short_token(self, too_short_token: str) -> None:
        with pytest.raises(InvalidGiftTokenError):
            build_bot_gift_claim_link(too_short_token, 'test_bot')

    @pytest.mark.parametrize(
        'malformed_token',
        [
            None,
            'token with spaces',
            'token!with#special$',
            'token\nwith\nnewlines',
            'токен_с_юникодом_123456789012345678901234567890123456',
        ],
    )
    def test_rejects_malformed_token(self, malformed_token: str | None) -> None:
        with pytest.raises(InvalidGiftTokenError):
            build_bot_gift_claim_link(malformed_token, 'test_bot')  # type: ignore[arg-type]


class TestBuildCabinetGiftClaimLink:
    """Tests for build_cabinet_gift_claim_link."""

    def test_preserves_full_token_and_builds_canonical_url(self) -> None:
        token = generate_purchase_token()
        link = build_cabinet_gift_claim_link(token, 'https://vpn.example.com')
        assert link == f'https://vpn.example.com/buy/gift/{token}'

    @pytest.mark.parametrize(
        ('cabinet_url', 'expected_base'),
        [
            ('https://vpn.example.com', 'https://vpn.example.com'),
            ('https://vpn.example.com/', 'https://vpn.example.com'),
            ('https://vpn.example.com///', 'https://vpn.example.com'),
            ('http://localhost:8000', 'http://localhost:8000'),
            ('  https://cabinet.remnawave.io/  ', 'https://cabinet.remnawave.io'),
        ],
    )
    def test_cabinet_url_slash_and_whitespace_normalization(self, cabinet_url: str, expected_base: str) -> None:
        token = generate_purchase_token()
        link = build_cabinet_gift_claim_link(token, cabinet_url)
        assert link == f'{expected_base}/buy/gift/{token}'

    @pytest.mark.parametrize('missing_url', [None, '', '   '])
    def test_rejects_missing_cabinet_url(self, missing_url: str | None) -> None:
        token = generate_purchase_token()
        with pytest.raises(MissingCabinetUrlError):
            build_cabinet_gift_claim_link(token, missing_url)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        'invalid_url',
        ['ftp://cabinet.com', 'cabinet.example.com', 'not a url', '://missing-scheme'],
    )
    def test_rejects_invalid_cabinet_url(self, invalid_url: str) -> None:
        token = generate_purchase_token()
        with pytest.raises(InvalidCabinetUrlError):
            build_cabinet_gift_claim_link(token, invalid_url)

    def test_rejects_invalid_token(self) -> None:
        with pytest.raises(InvalidGiftTokenError):
            build_cabinet_gift_claim_link('short', 'https://cabinet.example.com')


class TestBuildTelegramGiftShareUrl:
    """Tests for build_telegram_gift_share_url."""

    def test_produces_canonical_telegram_share_url(self) -> None:
        claim_link = 'https://t.me/test_bot?start=GIFT_abcdef123456'
        share_text = 'Вам подарок! Активируйте подписку по ссылке.'

        share_url = build_telegram_gift_share_url(claim_link, share_text)

        parsed = urllib.parse.urlparse(share_url)
        assert parsed.scheme == 'https'
        assert parsed.netloc == 't.me'
        assert parsed.path == '/share/url'

        qs = urllib.parse.parse_qs(parsed.query)
        assert qs['url'] == [claim_link]
        assert qs['text'] == [share_text]

    def test_independently_url_encodes_reserved_characters_and_unicode(self) -> None:
        claim_link = 'https://t.me/test_bot?start=GIFT_123&foo=bar#section'
        share_text = '🎁 Подарок для тебя!\nПлан: Premium (30 дней) & бонус = 100%'

        share_url = build_telegram_gift_share_url(claim_link, share_text)

        parsed = urllib.parse.urlparse(share_url)
        qs = urllib.parse.parse_qs(parsed.query)

        assert qs['url'][0] == claim_link
        assert qs['text'][0] == share_text

    @pytest.mark.parametrize(
        'invalid_claim_link',
        [None, '', '   ', 'not-a-url', 'ftp://bad-scheme.com'],
    )
    def test_rejects_invalid_claim_link(self, invalid_claim_link: str | None) -> None:
        with pytest.raises(InvalidClaimLinkError):
            build_telegram_gift_share_url(invalid_claim_link, 'Gift text')  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        'invalid_share_text',
        [None, '', '   ', 123, []],
    )
    def test_rejects_invalid_or_empty_share_text(self, invalid_share_text: str | None) -> None:
        claim_link = 'https://t.me/test_bot?start=GIFT_123'
        with pytest.raises(InvalidShareTextError):
            build_telegram_gift_share_url(claim_link, invalid_share_text)  # type: ignore[arg-type]

    def test_share_text_contains_no_price_and_no_raw_token(self) -> None:
        """Verify contract: share payload contains claim URL, but no prices or raw tokens."""
        raw_token = generate_purchase_token()
        bot_link = build_bot_gift_claim_link(raw_token, 'my_bot')
        share_text = 'Вам подарили подписку на 30 дней! Нажмите на ссылку, чтобы активировать её.'

        share_url = build_telegram_gift_share_url(bot_link, share_text)
        parsed = urllib.parse.urlparse(share_url)
        qs = urllib.parse.parse_qs(parsed.query)

        decoded_text = qs['text'][0]
        # Must not contain raw token
        assert raw_token not in decoded_text
        # Must not leak financial details
        assert '₽' not in decoded_text
        assert 'rub' not in decoded_text.lower()
        assert 'kopek' not in decoded_text.lower()
        assert 'price' not in decoded_text.lower()

    def test_exceptions_inherit_from_base_gift_link_error(self) -> None:
        assert issubclass(InvalidGiftTokenError, GiftLinkError)
        assert issubclass(InvalidBotUsernameError, GiftLinkError)
        assert issubclass(MissingBotUsernameError, InvalidBotUsernameError)
        assert issubclass(InvalidCabinetUrlError, GiftLinkError)
        assert issubclass(MissingCabinetUrlError, InvalidCabinetUrlError)
        assert issubclass(InvalidClaimLinkError, GiftLinkError)
        assert issubclass(InvalidShareTextError, GiftLinkError)


class TestLandingGiftLinkIntegration:
    """Tests for landing route purchase status link generation."""

    def test_build_purchase_status_response_uses_canonical_links(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.cabinet.routes.landing import _build_purchase_status_response
        from app.config import settings
        from app.database.models import GuestPurchase, GuestPurchaseStatus

        monkeypatch.setattr(settings, 'CABINET_URL', 'https://cabinet.example.com/')
        monkeypatch.setattr(settings, 'BOT_USERNAME', 'my_landing_bot')

        token = generate_purchase_token()
        purchase = GuestPurchase(
            id=1,
            token=token,
            is_gift=True,
            status=GuestPurchaseStatus.PAID.value,
            period_days=30,
        )

        response = _build_purchase_status_response(purchase)

        assert response.is_claimable is True
        assert response.claim_url == f'https://cabinet.example.com/buy/gift/{token}'
        assert response.bot_claim_link == f'https://t.me/my_landing_bot?start=GIFT_{token[:59]}'
        assert len(response.bot_claim_link.split('start=')[1]) == 64
        # Assert legacy 12-char slice is NOT used
        assert response.bot_claim_link != f'https://t.me/my_landing_bot?start=GIFT_{token[:12]}'
