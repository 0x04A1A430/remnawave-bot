from app.config import settings


def test_available_languages_default_contains_fa(monkeypatch):
    monkeypatch.setattr(settings, 'AVAILABLE_LANGUAGES', '', raising=False)
    languages = settings.get_available_languages()
    assert 'fa' in languages


def test_available_languages_normalizes_and_deduplicates(monkeypatch):
    monkeypatch.setattr(settings, 'AVAILABLE_LANGUAGES', 'ru,en,fa,FA,fa-IR', raising=False)
    languages = settings.get_available_languages()
    assert languages[0] == 'ru'
    assert 'en' in languages
    assert 'fa' in languages
    assert 'FA' not in languages
