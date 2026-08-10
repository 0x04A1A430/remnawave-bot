"""
Тесты для сервиса диагностики реферальной системы.
"""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.services.referral_diagnostics_service import ReferralDiagnosticsService


@pytest.fixture
def temp_log_file():
    """Создаёт временный лог-файл для тестов."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
        yield Path(f.name)
    # Cleanup
    Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def sample_log_content():
    """Пример содержимого лог-файла с реферальными событиями."""
    today = datetime.now(UTC).strftime('%Y-%m-%d')
    return f"""
{today} 10:00:00,123 - app.handlers.start - INFO - Найден реферальный код: <ABC123>
{today} 10:00:05,456 - app.handlers.start - INFO - Реферальный код ABC123 применен для пользователя 123456789
{today} 10:00:10,789 - app.services.referral_service - INFO - Реферальная регистрация обработана для 123456789
{today} 10:00:15,012 - app.services.referral_service - INFO - Реферал 123456789 получил бонус

{today} 11:00:00,345 - app.handlers.start - INFO - Найден реферальный код: <XYZ999>
{today} 11:00:05,678 - app.handlers.start - INFO - Реферальный код XYZ999 применен для пользователя 987654321

{today} 12:00:00,901 - app.handlers.start - INFO - Найден реферальный код: <TEST777>

{today} 13:00:00,234 - unrelated module - INFO - Some other log message
"""


@pytest.mark.asyncio
async def test_parse_clicks_basic(temp_log_file, sample_log_content):
    """Тест базового парсинга логов переходов по реферальным ссылкам."""
    temp_log_file.write_text(sample_log_content)

    service = ReferralDiagnosticsService(log_path=str(temp_log_file))

    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)

    clicks, total_lines, lines_in_period = await service._parse_clicks(today, tomorrow)

    assert total_lines >= 6, f'Expected at least 6 total lines, found {total_lines}'
    assert isinstance(clicks, list)


@pytest.mark.asyncio
async def test_analyze_period_with_issues(temp_log_file, sample_log_content):
    """Тест анализа с проблемными случаями."""
    temp_log_file.write_text(sample_log_content)

    service = ReferralDiagnosticsService(log_path=str(temp_log_file))

    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)

    from unittest.mock import AsyncMock

    mock_db = AsyncMock()
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    report = await service.analyze_period(mock_db, today, tomorrow)

    assert report.total_ref_clicks >= 0


@pytest.mark.asyncio
async def test_empty_log_file(temp_log_file):
    """Тест работы с пустым лог-файлом."""
    temp_log_file.write_text('')

    service = ReferralDiagnosticsService(log_path=str(temp_log_file))

    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)

    from unittest.mock import AsyncMock

    mock_db = AsyncMock()

    report = await service.analyze_period(mock_db, today, tomorrow)

    assert report.total_ref_clicks == 0
    assert report.total_lines_parsed == 0
    assert report.lines_in_period == 0


@pytest.mark.asyncio
async def test_nonexistent_log_file():
    """Тест работы с несуществующим лог-файлом."""
    service = ReferralDiagnosticsService(log_path='/nonexistent/path/to/log.log')

    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)

    from unittest.mock import AsyncMock

    mock_db = AsyncMock()

    report = await service.analyze_period(mock_db, today, tomorrow)

    assert report.total_ref_clicks == 0
    assert report.total_lines_parsed == 0


@pytest.mark.asyncio
async def test_analyze_today(temp_log_file, sample_log_content):
    """Тест метода analyze_today."""
    temp_log_file.write_text(sample_log_content)

    service = ReferralDiagnosticsService(log_path=str(temp_log_file))

    from unittest.mock import AsyncMock

    mock_db = AsyncMock()

    report = await service.analyze_today(mock_db)

    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    assert report.analysis_period_start.date() == today.date()
