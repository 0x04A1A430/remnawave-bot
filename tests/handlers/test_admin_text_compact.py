"""Финальные админские экраны не должны содержать пустых разрывов."""

from app.handlers.admin import monitoring, pricing


def test_rendered_admin_screens_have_no_double_blank_lines():
    screens = [
        pricing._build_overview('ru')[0],
        pricing._build_section('periods', 'ru')[0],
        monitoring._build_traffic_settings_text(language='ru'),
    ]

    for text in screens:
        assert text
        assert '\n\n\n' not in text
        assert text == text.strip()
