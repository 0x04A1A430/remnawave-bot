"""Shared formatting utilities for traffic, price, and period display."""


def format_traffic(gb: int) -> str:
    """Форматирует трафик."""
    if gb == 0:
        return 'Безлимит'
    return f'{gb} ГБ'


def format_price_kopeks(kopeks: int, compact: bool = False) -> str:
    """Форматирует цену из копеек в рубли."""
    rubles = kopeks / 100
    if compact:
        # Компактный формат - округляем до рублей
        return f'{int(round(rubles))}₽'
    if rubles == int(rubles):
        return f'{int(rubles)} ₽'
    return f'{rubles:.2f} ₽'


def format_period(days: int) -> str:
    """Форматирует период."""
    if days == 1:
        return '1 день'
    if days < 5:
        return f'{days} дня'
    if days < 21 or days % 10 >= 5 or days % 10 == 0:
        return f'{days} дней'
    if days % 10 == 1:
        return f'{days} день'
    return f'{days} дня'
