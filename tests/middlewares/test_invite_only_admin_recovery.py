from types import SimpleNamespace


def test_blocked_admin_is_not_stopped_before_registration_gate(monkeypatch):
    from app.middlewares import auth

    monkeypatch.setattr(type(auth.settings), 'is_admin', lambda self, telegram_id: telegram_id == 42)
    blocked = SimpleNamespace(status='blocked', telegram_id=42)
    ordinary = SimpleNamespace(status='blocked', telegram_id=43)

    assert auth._is_blocked_non_admin(blocked) is False
    assert auth._is_blocked_non_admin(ordinary) is True
