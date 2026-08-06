"""Tests for inline gift flag parsing (mixable -p/-t/-b/-d) and preview rendering."""

from app.handlers.admin.inline_gift import (
    _FOREVER_DAYS,
    _build_combo_caption,
    _build_subscription_caption,
    _build_syntax_hint,
    _flag_hint,
    _gift_summary,
    _parse_query,
    _temp_body_line,
)
from app.handlers.inline_gift import _build_info_text


class FakeTexts:
    """Returns the supplied default for every key — deterministic previews."""

    def t(self, key, default=None):
        return default


texts = FakeTexts()


class TestParseLegacySubscription:
    def test_plain_days(self):
        p = _parse_query('@user 30')
        assert p.username == 'user'
        assert p.days == 30
        assert p.traffic_gb is None
        assert p.devices is None
        assert p.has_subscription
        assert not p.is_combo

    def test_days_traffic_devices(self):
        p = _parse_query('@user 30 500 3')
        assert p.days == 30
        assert p.traffic_gb == 500
        assert p.devices == 3

    def test_dash_skip(self):
        p = _parse_query('@user 30 - 3')
        assert p.days == 30
        assert p.traffic_gb is None
        assert p.devices == 3

    def test_forever_days(self):
        p = _parse_query('@user -1')
        assert p.days == _FOREVER_DAYS

    def test_target_by_id(self):
        p = _parse_query('123456789 30')
        assert p.target_id == 123456789
        assert p.days == 30

    def test_explicit_p_flag_equivalent(self):
        p = _parse_query('@user -p 30 500 3')
        assert p.days == 30
        assert p.traffic_gb == 500
        assert p.devices == 3

    def test_p_forever(self):
        p = _parse_query('@user -p -1')
        assert p.days == _FOREVER_DAYS

    def test_empty_info(self):
        p = _parse_query('')
        assert p.username == ''
        assert not p.has_any

    def test_unrecognized_flag_is_info(self):
        p = _parse_query('@user -x')
        assert not p.has_any


class TestParseSingleMixables:
    def test_discount(self):
        p = _parse_query('@user -d 15')
        assert p.has_discount
        assert p.discount_percent == 15
        assert not p.is_combo

    def test_discount_clamped(self):
        assert _parse_query('@user -d 200').discount_percent == 99
        assert _parse_query('@user -d 0').discount_percent == 1

    def test_balance(self):
        p = _parse_query('@user -b 1500')
        assert p.has_balance
        assert p.balance_rub == 1500

    def test_temp_default_days(self):
        p = _parse_query('@user -t 100')
        assert p.has_temp
        assert p.temp_traffic_gb == 100
        assert p.temp_traffic_days == 0

    def test_temp_with_days(self):
        p = _parse_query('@user -t 100 60')
        assert p.temp_traffic_gb == 100
        assert p.temp_traffic_days == 60

    def test_temp_negative_gb(self):
        p = _parse_query('@user -t -100')
        assert p.temp_traffic_gb == -100
        assert p.temp_traffic_days == 0

    def test_temp_clamped(self):
        assert _parse_query('@user -t 5000').temp_traffic_gb == 999
        assert _parse_query('@user -t -5000').temp_traffic_gb == -999


class TestParseMixing:
    def test_full_mix(self):
        p = _parse_query('@user -p 30 500 3 -t 100 60 -b 1500 -d 15')
        assert p.has_subscription
        assert p.days == 30
        assert p.traffic_gb == 500
        assert p.devices == 3
        assert p.has_temp
        assert p.temp_traffic_gb == 100
        assert p.temp_traffic_days == 60
        assert p.has_balance
        assert p.balance_rub == 1500
        assert p.has_discount
        assert p.discount_percent == 15
        assert p.is_combo

    def test_mix_any_order(self):
        a = _parse_query('@user -t 100 -b 500')
        b = _parse_query('@user -b 500 -t 100')
        assert a.temp_traffic_gb == b.temp_traffic_gb == 100
        assert a.balance_rub == b.balance_rub == 500

    def test_p_stops_at_next_flag(self):
        p = _parse_query('@user -p 30 -b 500')
        assert p.days == 30
        assert p.balance_rub == 500
        assert p.traffic_gb is None

    def test_p_with_skip_then_temp(self):
        p = _parse_query('@user -p 30 - 3 -t 100')
        assert p.days == 30
        assert p.traffic_gb is None
        assert p.devices == 3
        assert p.has_temp

    def test_discount_plus_balance_is_combo(self):
        p = _parse_query('@user -d 15 -b 500')
        assert p.is_combo

    def test_subscription_only_not_combo(self):
        p = _parse_query('@user -p 30')
        assert p.has_subscription
        assert not p.is_combo


class TestParseMulti:
    def test_multi_full(self):
        p = _parse_query('-r 5 30 500 3')
        assert p.is_multi
        assert p.multi_count == 5
        assert p.days == 30
        assert p.traffic_gb == 500
        assert p.devices == 3

    def test_multi_count_only(self):
        p = _parse_query('-r 3')
        assert p.multi_count == 3
        assert p.days is None


class TestCaptions:
    def test_temp_body_line(self):
        assert _temp_body_line(100, 60, texts) == '+100 ГБ трафика (на 60 дн.)'
        assert _temp_body_line(100, 0, texts) == '+100 ГБ трафика (на 0 дн.)'

    def test_combo_caption_all_components(self):
        p = _parse_query('@user -p 30 500 3 -t 100 60 -b 1500 -d 15')
        cap = _build_combo_caption('user', p, texts)
        assert 'Подарочная подписка для <code>user</code>' in cap
        assert '30 дн.' in cap
        assert '500 ГБ' in cap
        assert '3 уст.' in cap
        assert 'Скидка 15%' in cap
        assert '+1500 ₽ на баланс' in cap
        assert '+100 ГБ трафика (на 60 дн.)' in cap

    def test_combo_caption_temp_default_days(self):
        p = _parse_query('@user -b 500 -t 100')
        cap = _build_combo_caption('user', p, texts)
        assert '+100 ГБ трафика (на 30 дн.)' in cap

    def test_subscription_caption_multi(self):
        cap = _build_subscription_caption('', 30, 500, 3, texts, multi_count=5)
        assert 'Подарочная подписка — первым 5' in cap
        assert '30 дн.' in cap

    def test_subscription_caption_empty_body(self):
        cap = _build_subscription_caption('user', None, None, None, texts)
        assert '<blockquote>—</blockquote>' in cap

    def test_gift_summary(self):
        assert _gift_summary(30, 500, 3, texts) == '30 дн., 500 ГБ, 3 уст.'
        assert _gift_summary(None, None, None, texts) == '—'

    def test_html_escape_in_caption(self):
        p = _parse_query('@<b> 30')
        cap = _build_combo_caption('<script>', p, texts)
        assert '<script>' not in cap
        assert '&lt;script&gt;' in cap


class TestHints:
    def test_syntax_hints_insert_text_without_sending(self):
        hints = _build_syntax_hint(texts)
        ids = [h.id for h in hints]
        assert 'hint_sub' in ids
        assert 'hint_mix' in ids
        assert 'hint_t' in ids
        for h in hints:
            assert getattr(h, 'input_message_content', None) is None
        by_id = {h.id: h for h in hints}
        assert by_id['hint_t'].title == '@user -t 100 60'

    def test_flag_hint_branches(self):
        assert 'временный трафик' in _flag_hint('@user -t 100 60', texts)
        assert 'скидка 15%' in _flag_hint('@user -d 15', texts)
        assert 'пополнить баланс' in _flag_hint('@user -b 500', texts)
        assert '-p' in _flag_hint('@user -p 30', texts)
        assert 'миксируются' in _flag_hint('@user', texts)


class TestActivationPreview:
    def test_temp_traffic_preview(self):
        text = _build_info_text(
            None,
            None,
            None,
            texts,
            gift_type='temp_traffic',
            temp_traffic_gb=100,
            temp_traffic_days=60,
        )
        assert '+100 ГБ трафика (на 60 дн.)' in text

    def test_temp_traffic_preview_default_days(self):
        text = _build_info_text(
            None,
            None,
            None,
            texts,
            gift_type='temp_traffic',
            temp_traffic_gb=100,
            temp_traffic_days=0,
        )
        assert '+100 ГБ трафика (на 30 дн.)' in text

    def test_balance_preview(self):
        text = _build_info_text(
            None,
            None,
            None,
            texts,
            gift_type='balance',
            balance_amount_kopeks=150000,
        )
        assert '+1500 ₽ на баланс' in text

    def test_combo_preview_components(self):
        text = _build_info_text(
            30,
            500,
            3,
            texts,
            gift_type='combo',
            balance_amount_kopeks=150000,
            temp_traffic_gb=100,
            temp_traffic_days=60,
            discount_percent=15,
        )
        assert 'Срок: <b>30 дней</b>' in text
        assert 'Скидка: <b>15%</b>' in text
        assert 'Баланс: <b>+1500 ₽</b>' in text
        assert 'Временный трафик: <b>+100 ГБ (на 60 дн.)</b>' in text

    def test_subscription_preview_no_existing(self):
        text = _build_info_text(30, 500, 3, texts, existing_sub=None)
        assert 'Срок: <b>30 дней</b>' in text
        assert 'Трафик: <b>500 ГБ</b>' in text
        assert 'Устройств: <b>3</b>' in text
