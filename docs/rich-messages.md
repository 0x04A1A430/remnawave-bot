# Rich-сообщения во всём боте

Бот умеет прозрачно отправлять обычные экраны как rich-сообщения (Bot API 10.1+),
не переписывая хендлеры: текст, форматирование и inline-клавиатура остаются
прежними. Реализация — `app/utils/rich_transport.py`, подключается в
`app/bot_factory.py` ко всем ботам, созданным через `create_bot()`.

## Что конвертируется

| Классический вызов | Rich-эквивалент |
|---|---|
| `sendMessage` | `sendRichMessage` (тот же HTML, `\n` → `<br/>`, `<pre>` не трогаем) |
| `editMessageText` | `editMessageText` с `rich_message` (правка на месте) |
| `sendPhoto` с логотипом бота | `sendRichMessage` с `<img src="логотип-URL"/>` + подпись |
| `sendPhoto` с любым другим фото (QR подарка, вложение тикета, картинка новости) | `sendRichMessage` с медиа-блоком `tg://photo?id=…` + подпись |

Клавиатуры (`InlineKeyboardMarkup`, `ReplyKeyboardMarkup`, `ForceReply`,
`ReplyKeyboardRemove`) передаются как есть.

## Флаги

```env
RICH_MESSAGES_ENABLED=true
# Публичный URL логотипа для rich-экранов. Пусто — MAIN_MENU_RICH_LOGO_URL.
RICH_MESSAGES_LOGO_URL=
```

`RICH_MESSAGES_ENABLED=false` мгновенно возвращает боту классический рендер
(без пересборки смысла экранов — только транспорт).

## Фоллбеки

- Любая ошибка `BadRequest`/`NotFound` при отправке rich-сообщения → тот же
  вызов повторяется классическим методом. Потерять экран нельзя.
- Если сервер вообще не знает `sendRichMessage` (`unknown method` / 404),
  rich-транспорт отключается до рестарта и пишет одно предупреждение в лог.
- Markdown-разметка и явные `entities` не конвертируются — остаются классикой.

## Совместимость со старым кодом

Rich-сообщения не имеют `.text` / `.caption`, поэтому:

- `RichMessageCompatMiddleware` перед хендлером заполняет `.text` читаемым
  текстом из блоков rich-сообщения (только в памяти процесса);
- `photo_message.edit_or_answer_photo` и `message_patch._edit_with_photo`
  считают rich-сообщение редактируемым текстом, а не медиа;
- экраны, которым нужно заменить rich-текст на фото (QR подарка/рефералки,
  QR оплаты), сами удаляют rich-сообщение и отправляют новое — иначе
  `edit_media` по rich-сообщению падает.

## Что осталось классическим

- альбомы (`sendMediaGroup`) и документы (`sendDocument`, чеки/логи/экспорты) —
  это функциональные вложения, а не экраны меню;
- inline-режим (`answerInlineQuery`) — у него собственные типы результатов;
- Markdown-сообщения и сообщения с `entities`.

## Тесты

`tests/utils/test_rich_transport.py` — конвертация текста/edit/логотипа/QR,
сохранение `<pre>`, отказ от Markdown, извлечение текста из rich-блоков.
