import html as html_module
import re
from datetime import datetime


ALLOWED_HTML_TAGS = {
    'b',
    'strong',  # жирный
    'i',
    'em',  # курсив
    'u',
    'ins',  # подчёркнутый
    's',
    'strike',
    'del',  # зачёркнутый
    'code',  # моноширинный
    'pre',  # блок кода
    'a',  # ссылка
    'blockquote',  # цитата
    'tg-spoiler',  # спойлер
    'tg-emoji',  # кастомный эмодзи
    'span',  # для class="tg-spoiler"
}

SELF_CLOSING_TAGS = {'br', 'hr', 'img'}

# Разрешённые атрибуты для HTML-тегов
ALLOWED_TAG_ATTRIBUTES = {
    'a': {'href'},
    'tg-emoji': {'emoji-id'},
    'span': {'class'},
}

# Разрешённые URI-схемы в href (allowlist вместо blocklist)
SAFE_URI_SCHEMES = re.compile(r'^(https?://|tg://|mailto:|tel:)', re.IGNORECASE)


def validate_email(email: str) -> bool:
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def validate_phone(phone: str) -> bool:
    pattern = r'^\+?[1-9]\d{1,14}$'
    cleaned_phone = re.sub(r'[\s\-\(\)]', '', phone)
    return re.match(pattern, cleaned_phone) is not None


def validate_telegram_username(username: str) -> bool:
    if not username:
        return False
    username = username.lstrip('@')
    pattern = r'^[a-zA-Z0-9_]{5,32}$'
    return re.match(pattern, username) is not None


def validate_promocode(code: str) -> bool:
    if not code or len(code) < 3 or len(code) > 20:
        return False
    return code.replace('_', '').replace('-', '').isalnum()


def validate_amount(amount_str: str, min_amount: float = 0, max_amount: float = float('inf')) -> float | None:
    try:
        amount = float(amount_str.replace(',', '.'))
        if min_amount <= amount <= max_amount:
            return amount
        return None
    except (ValueError, TypeError):
        return None


def validate_positive_integer(value: str | int, max_value: int = None) -> int | None:
    try:
        num = int(value)
        if num > 0 and (max_value is None or num <= max_value):
            return num
        return None
    except (ValueError, TypeError):
        return None


def validate_date_string(date_str: str, date_format: str = '%Y-%m-%d') -> datetime | None:
    try:
        return datetime.strptime(date_str, date_format)
    except ValueError:
        return None


def validate_url(url: str) -> bool:
    pattern = r'^https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)$'
    return re.match(pattern, url) is not None


def validate_uuid(uuid_str: str) -> bool:
    pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    return re.match(pattern, uuid_str.lower()) is not None


def validate_traffic_amount(traffic_str: str) -> int | None:
    traffic_str = traffic_str.upper().strip()

    if traffic_str in ['UNLIMITED', 'БЕЗЛИМИТ', '∞']:
        return 0

    units = {'MB': 1, 'GB': 1024, 'TB': 1024 * 1024, 'МБ': 1, 'ГБ': 1024, 'ТБ': 1024 * 1024}

    for unit, multiplier in units.items():
        if traffic_str.endswith(unit):
            try:
                value = float(traffic_str[: -len(unit)].strip())
                return int(value * multiplier)
            except ValueError:
                break

    try:
        return int(float(traffic_str))
    except ValueError:
        return None


def validate_subscription_period(days: str | int) -> int | None:
    try:
        days_int = int(days)
        if 1 <= days_int <= 3650:
            return days_int
        return None
    except (ValueError, TypeError):
        return None


def sanitize_html(text: str) -> str:
    """
    Безопасно санитизирует HTML-текст, заменяя HTML-сущности на соответствующие теги,
    при этом предотвращая XSS-уязвимости за счет безопасной обработки атрибутов.

    Args:
        text (str): Текст с HTML-сущностями (например, &lt;b&gt; жирный &lt;/b&gt;)

    Returns:
        str: Санитизированный HTML-текст (например, <b> жирный </b>)
    """
    if not text:
        return text

    # Для безопасности нужно обработать разрешенные теги, заменяя их сущности на теги
    # Но при этом безопасно обрабатывая атрибуты, чтобы избежать XSS

    allowed_tags = ALLOWED_HTML_TAGS.union(SELF_CLOSING_TAGS)

    # Обработка всех разрешенных тегов
    for tag in allowed_tags:
        # Паттерн: захватываем &lt;tag&gt;, &lt;/tag&gt;, или &lt;tag атрибуты&gt;
        # [^>]*? - ленивый захват до >
        pattern = rf'(&lt;)(/?{tag}\b)([^>]*?)(&gt;)'

        tag_lower = tag.lower()

        def replace_tag(match, _tag=tag_lower):
            full_tag_content = match.group(2)  # /?tagname
            attrs_part = match.group(3).removeprefix(' ')  # атрибуты (без >)

            if not attrs_part:
                return f'<{full_tag_content}>'

            # Полное декодирование HTML-сущностей для корректной проверки атрибутов
            processed_attrs = html_module.unescape(attrs_part)

            # Проверяем whitelist атрибутов для данного тега
            allowed_attrs = ALLOWED_TAG_ATTRIBUTES.get(_tag)
            if allowed_attrs is None:
                # Тег без whitelist — удаляем ВСЕ атрибуты
                return f'<{full_tag_content}>'

            filtered_parts = []
            for attr_match in re.finditer(r'([a-zA-Z][\w-]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', processed_attrs):
                attr_name = attr_match.group(1).lower()
                attr_value = attr_match.group(2) if attr_match.group(2) is not None else attr_match.group(3)
                if attr_name not in allowed_attrs:
                    continue
                # href: allowlist безопасных URI-схем
                if attr_name == 'href':
                    # Нормализуем: убираем control chars и пробелы из начала значения
                    normalized = re.sub(r'[\x00-\x1f\x7f\s]+', '', attr_value)
                    if not SAFE_URI_SCHEMES.match(normalized):
                        continue
                filtered_parts.append(f'{attr_name}="{attr_value}"')
            processed_attrs = ' '.join(filtered_parts)

            if processed_attrs:
                return f'<{full_tag_content} {processed_attrs}>'
            return f'<{full_tag_content}>'

        text = re.sub(pattern, replace_tag, text, flags=re.IGNORECASE)

    return text


def sanitize_telegram_name(name: str | None) -> str | None:
    """Санитизация Telegram-имени для безопасной вставки в HTML и хранения.
    Заменяет угловые скобки и амперсанд на безопасные визуальные аналоги.
    """
    if not name:
        return name
    try:
        return name.replace('<', '‹').replace('>', '›').replace('&', '＆').strip()
    except Exception:
        return name


def validate_device_count(count: str | int) -> int | None:
    try:
        count_int = int(count)
        if 1 <= count_int <= 10:
            return count_int
        return None
    except (ValueError, TypeError):
        return None


def validate_referral_code(code: str) -> bool:
    if not code:
        return False

    if code.startswith('ref') and len(code) > 3:
        user_id_part = code[3:]
        return user_id_part.isdigit()

    return validate_promocode(code)


def validate_html_tags(text: str) -> tuple[bool, str]:
    if not text:
        return True, ''

    tag_pattern = r'<(/?)([a-zA-Z][a-zA-Z0-9-]*)[^>]*>'
    tags = re.findall(tag_pattern, text)

    for is_closing, tag_name in tags:
        tag_name_lower = tag_name.lower()

        if tag_name_lower not in ALLOWED_HTML_TAGS and tag_name_lower not in SELF_CLOSING_TAGS:
            return False, f'Неподдерживаемый тег: <{tag_name}>'

    return validate_html_structure(text)


def validate_html_structure(text: str) -> tuple[bool, str]:
    tag_pattern = r'<(/?)([a-zA-Z][a-zA-Z0-9-]*)[^>]*?/?>'

    matches = re.finditer(tag_pattern, text)
    tag_stack = []

    for match in matches:
        full_tag = match.group(0)
        is_closing = bool(match.group(1))
        tag_name = match.group(2).lower()

        if full_tag.endswith('/>') or tag_name in SELF_CLOSING_TAGS:
            continue

        if not is_closing:
            tag_stack.append(tag_name)
        else:
            if not tag_stack:
                return False, f'Закрывающий тег без открывающего: </{tag_name}>'

            last_tag = tag_stack.pop()
            if last_tag != tag_name:
                return False, f'Неправильная вложенность тегов: ожидался </{last_tag}>, найден </{tag_name}>'

    if tag_stack:
        return False, f'Незакрытый тег: <{tag_stack[-1]}>'

    return True, ''


def fix_html_tags(text: str) -> str:
    if not text:
        return text

    fixes = [
        (r'<a href=([^"\s>]+)>', r'<a href="\1">'),
        (r'<(br|hr|img[^>]*?)>', r'<\1 />'),
        (r'<<([^>]+)>>', r'<\1>'),
        (r'<\s+([^>]+)\s+>', r'<\1>'),
    ]

    result = text
    for pattern, replacement in fixes:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    return result


def get_html_help_text() -> str:
    return """<b>Поддерживаемые HTML теги:</b>

• <code>&lt;b&gt;жирный&lt;/b&gt;</code> или <code>&lt;strong&gt;&lt;/strong&gt;</code>
• <code>&lt;i&gt;курсив&lt;/i&gt;</code> или <code>&lt;em&gt;&lt;/em&gt;</code>
• <code>&lt;u&gt;подчёркнутый&lt;/u&gt;</code>
• <code>&lt;s&gt;зачёркнутый&lt;/s&gt;</code>
• <code>&lt;code&gt;моноширинный&lt;/code&gt;</code>
• <code>&lt;pre&gt;блок кода&lt;/pre&gt;</code>
• <code>&lt;a href="url"&gt;ссылка&lt;/a&gt;</code>
• <code>&lt;blockquote&gt;цитата&lt;/blockquote&gt;</code>
• <code>&lt;tg-spoiler&gt;спойлер&lt;/tg-spoiler&gt;</code>
• <code>&lt;tg-emoji emoji-id="123"&gt;😀&lt;/tg-emoji&gt;</code>

<b>⚠️ Важные правила:</b>
• Каждый открывающий тег должен быть закрыт
• Теги должны быть правильно вложены
• Атрибуты ссылок берите в кавычки

<b>❌ Неправильно:</b>
<code>&lt;b&gt;жирный &lt;i&gt;курсив&lt;/b&gt;&lt;/i&gt;</code>

<b>✅ Правильно:</b>
<code>&lt;b&gt;жирный &lt;i&gt;курсив&lt;/i&gt;&lt;/b&gt;</code>"""


def validate_rules_content(text: str) -> tuple[bool, str, str | None]:
    if not text or not text.strip():
        return False, 'Текст правил не может быть пустым', None

    if len(text) > 4000:
        return False, f'Текст слишком длинный: {len(text)} символов (максимум 4000)', None

    is_valid_html, html_error = validate_html_tags(text)
    if not is_valid_html:
        fixed_text = fix_html_tags(text)
        fixed_is_valid, _ = validate_html_tags(fixed_text)

        if fixed_is_valid and fixed_text != text:
            return False, html_error, fixed_text
        return False, html_error, None

    return True, '', None
