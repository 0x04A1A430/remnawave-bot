# Changelog

## [0.2.1](https://github.com/cy7su/remnawave-bot/compare/v0.2.0...v0.2.1) (2026-07-13)


### Bug Fixes

* address CodeQL security alerts - weak hash, ReDoS, URL sanitization, exception exposure ([4c19317](https://github.com/cy7su/remnawave-bot/commit/4c19317868d217a5dcb74cbf5800d66d208926fd))
* remove unused variable after error message change ([b73e800](https://github.com/cy7su/remnawave-bot/commit/b73e800794361b1e6e246119c89dad2feef7e304))

## [0.2.0](https://github.com/cy7su/remnawave-bot/compare/v0.1.0...v0.2.0) (2026-07-13)


### Features

* Add emoji to 'Buy Traffic' button for improved visibility ([b9444ef](https://github.com/cy7su/remnawave-bot/commit/b9444ef7177ebddde70f90a65e50425f7b56fb50))
* Enhance inline gift functionality with discounts and balance top-ups ([35ac2c8](https://github.com/cy7su/remnawave-bot/commit/35ac2c8586cfd7d502cda38d67b45baf0a28c14d))
* enhance inline gift handling with multi-activation support ([99676dc](https://github.com/cy7su/remnawave-bot/commit/99676dc7f3c334c32837a07828603df70af90830))
* Make inline gift parameters nullable for better flexibility in handling subscriptions ([47c3a08](https://github.com/cy7su/remnawave-bot/commit/47c3a086b410e965a13f8a1b6bbbcce5e2d519ae))
* Update device management button and enhance info menu with emoji ([5df7477](https://github.com/cy7su/remnawave-bot/commit/5df74770f88a1ca71bdd06800ea3e1eea5c298ee))
* Update payment method name with emoji for better visibility ([78ddddd](https://github.com/cy7su/remnawave-bot/commit/78ddddd15d963e8b47154ac99660b3612cd87542))
* премиум-эмодзи для кнопки Настройки в подписке ([12fcf6b](https://github.com/cy7su/remnawave-bot/commit/12fcf6b4711d27bdfeaf7a87e8edc51207ecf854))


### Bug Fixes

* _check_recipient for unregistered users — allow [@username](https://github.com/username) gifts via deep link ([6507af9](https://github.com/cy7su/remnawave-bot/commit/6507af9eb79fb718e5774a25592f7dcc8066b2e8))
* 3 изменения - убрана подпись 'Скопируйте ссылку', исправлен HTML-truncate баг в устройствах, referral кнопки (зеленая/синяя) ([3a454fb](https://github.com/cy7su/remnawave-bot/commit/3a454fb47229b92bb1a8a5e2a81d4e3dc780109a))
* add debug logs for gift creation and recipient check ([0662017](https://github.com/cy7su/remnawave-bot/commit/0662017c4590de20a3995b91b4530fa02c5c6a22))
* add macOS, Linux to _PLATFORM_EMOJI across all files ([d953c18](https://github.com/cy7su/remnawave-bot/commit/d953c18e923701341055263caae79611a36ca915))
* Add missing sqlalchemy import to migration 0047 ([991f42f](https://github.com/cy7su/remnawave-bot/commit/991f42fb99b644c520abedb89c77eb177e38234f))
* add platform info to device added webhook notification ([ee5baeb](https://github.com/cy7su/remnawave-bot/commit/ee5baebdc8808c818b5adc31ba2f7b6b8c953672))
* Apply alembic migrations when DB is managed but not at head ([bdfe39f](https://github.com/cy7su/remnawave-bot/commit/bdfe39f7792fc2e13452027e66321d3806b69bb2))
* AttributeError 'Subscription' object has no attribute 'last_revoke_at' ([182b7fd](https://github.com/cy7su/remnawave-bot/commit/182b7fd76dc9b0402cca09e55f2b35a3d267994c))
* balance/temp_traffic gift preview, activation, premium emoji, platform name in device notification ([931844f](https://github.com/cy7su/remnawave-bot/commit/931844f2648ba2c6c04be81fc2b724bd2545c62b))
* change notification_settings JSON→JSONB for DISTINCT compat; skip migrations on managed DB if not at head ([5704b0f](https://github.com/cy7su/remnawave-bot/commit/5704b0ff32d148b0d3751e0e7fd12818b17e4866))
* check recipient after unregistered user check; restore sentinel logic ([4f1004d](https://github.com/cy7su/remnawave-bot/commit/4f1004d0929b703d3930abb46d26cd7f0363e202))
* chmod fallback on bind mount dirs; whitelist entrypoint.py in gitignore ([b7d0f59](https://github.com/cy7su/remnawave-bot/commit/b7d0f594f6ad7f836c3b5362cd85915c2d3db342))
* correct revoke cooldown timer display and change to 3 days ([8c9a23e](https://github.com/cy7su/remnawave-bot/commit/8c9a23e6b930edf5e74cbfd5eaa275943ead45da))
* escape tg-emoji values with double quotes in admin.py lines 193, 262 ([11e05a6](https://github.com/cy7su/remnawave-bot/commit/11e05a604170491baebc31746e847a7bed3129f6))
* Force Alembic upgrade for managed databases out of sync ([2b22442](https://github.com/cy7su/remnawave-bot/commit/2b22442826da97ea0ad8d3caf10eaaad1f0fb336))
* handle both time_left and revoke_cooldown placeholders; set cooldown to 12 hours ([dc00634](https://github.com/cy7su/remnawave-bot/commit/dc006347ce3e24cb42a57936e11e0f31e9dcd316))
* Make 0034_guest_purchase_recipient_warning migration idempotent ([e748b8a](https://github.com/cy7su/remnawave-bot/commit/e748b8a89c2af8bcab90842b65482edd977e1c17))
* Make 0036_add_riopay_payments migration idempotent ([cf6b289](https://github.com/cy7su/remnawave-bot/commit/cf6b28967ff92820ba8be49af27efde80eab82d2))
* Make 0040_add_severpay_payments migration idempotent ([8397675](https://github.com/cy7su/remnawave-bot/commit/83976756e555b5d8295a39334b2f3985f40ff7ff))
* make close button red using make_button with style=danger ([ea77844](https://github.com/cy7su/remnawave-bot/commit/ea778444e88eef228f12b4aa3510d7ff3b8919bd))
* Make migrations 0045-0098 idempotent ([1b0b0b4](https://github.com/cy7su/remnawave-bot/commit/1b0b0b4c51e7726c9c44e7848b7ac5d22ae9925d))
* pass real from_user to show_pending_inline_gift (callback.message.from_user is the bot) ([1815df4](https://github.com/cy7su/remnawave-bot/commit/1815df43f4837809d9703c3eedd32712e9d4e778))
* premium emoji in topup buttons, bs_ gift deep link handling, remove -bc flag ([077aab9](https://github.com/cy7su/remnawave-bot/commit/077aab9629397221c7afe05cde8bedffafc63582))
* remove 'Баланс пополнен автоматически!' from payment success messages ([93d4fa7](https://github.com/cy7su/remnawave-bot/commit/93d4fa7ad06c79438b9d9e6878dfc952a135e63e))
* remove begin_nested() from _run_safe to prevent InvalidRequestError; simplify expiring sub notification text; make extend button green ([a8e40f3](https://github.com/cy7su/remnawave-bot/commit/a8e40f3aca3b0fdcf0492a10e44517ea72881d08))
* remove end_date/autopay/action_text placeholders from SUBSCRIPTION_EXPIRING_PAID translations ([15849cd](https://github.com/cy7su/remnawave-bot/commit/15849cdb875a21679957e67a35319a9334ce731f))
* remove indented module-level _PLATFORM_EMOJI (dead code, broke import) ([bb18dca](https://github.com/cy7su/remnawave-bot/commit/bb18dcacd2fd38f39c23f29d39146f2252c6f4d1))
* remove invalid dependency-type from dependabot config ([a1fca18](https://github.com/cy7su/remnawave-bot/commit/a1fca185ca0745a3319db165f7154fcdb0a7afa6))
* remove shadowing local import of get_subscription_by_user_id (UnboundLocalError) ([415c750](https://github.com/cy7su/remnawave-bot/commit/415c750df137725d6855513ec5e7c0b5fe50b0e0))
* replace undefined _get_device_added_keyboard with _get_subscription_keyboard in _handle_device_added ([777f0e8](https://github.com/cy7su/remnawave-bot/commit/777f0e8ac26f865e755148cc7ff46351541b992e))
* restore field import for temp_traffic_gb ([5b29d69](https://github.com/cy7su/remnawave-bot/commit/5b29d69b780ddab7291b13e14b3715eead0e798e))
* show device model instead of OS in new device notification ([b95667e](https://github.com/cy7su/remnawave-bot/commit/b95667e5f5d65aeabdd2d5629b851085d2b56bcb))
* skip main menu when inline gift is shown after registration ([f36d6b8](https://github.com/cy7su/remnawave-bot/commit/f36d6b81fb8c3b62142b7b006f928345267b6bba))
* store intended_sentinel over inline_message_id for proper recipient check ([bc7138a](https://github.com/cy7su/remnawave-bot/commit/bc7138ade9025344a074c32f7059962ac5047bd5))
* temp traffic - only create TrafficPurchase, skip traffic_limit_gb; add debug log for recipient mismatch ([2b57609](https://github.com/cy7su/remnawave-bot/commit/2b57609893c1347eec05566188c0e054537da8e1))
* update revoke confirmation button callback for multi-tariff ([f044e91](https://github.com/cy7su/remnawave-bot/commit/f044e91ef60783b20a65ddca8eb0db7d41b55aac))
* update Russian locale with Telegram emoji for confirm button ([262aa7b](https://github.com/cy7su/remnawave-bot/commit/262aa7b78dfad803d27ac176b5133c31162207f6))
* восстановлен внешний вид страницы устройств из старого бота + исправлен HTML-truncate в кнопках ([a1d15f9](https://github.com/cy7su/remnawave-bot/commit/a1d15f9ee4b4780741e702f880e83e8e342c1a75))
* замена InlineKeyboardButton на make_button + copy_text для кнопки копирования ссылки ([a21b86e](https://github.com/cy7su/remnawave-bot/commit/a21b86e18ee1331452becadae27109cf644a6122))
* исправление ошибки HTML в меню, замена blockquote на code, удаление сообщения с правилами, обновление текста кнопки подключения ([542121c](https://github.com/cy7su/remnawave-bot/commit/542121ca542bf53ec0c88ee2adb55816f7f5e3d7))
* переносы строк в DEVICE_MANAGEMENT_OVERVIEW / CONNECTED_HEADER в locales/ru.json ([cc6a4d8](https://github.com/cy7su/remnawave-bot/commit/cc6a4d8920e738af14ddcc1df6546186601f0362))
* премиум эмодзи и URL для кнопки Подключится в MenuLayoutService ([6924cc3](https://github.com/cy7su/remnawave-bot/commit/6924cc3dda898f67a55ae2f00333169a65f68ce1))
* пустые кнопки пагинации (→/←), стрелки в referral, убраны emoji из SUBSCRIPTION_SETTINGS_OVERVIEW ([c11ad06](https://github.com/cy7su/remnawave-bot/commit/c11ad065d0ba0142aad6c5b8a9e37d70d3567454))
* редизайн Настроек подписки - убраны текущие параметры, добавлена сводка (трафик/дата/устройства) и таймер до перевыпуска ([4ef7471](https://github.com/cy7su/remnawave-bot/commit/4ef7471596a53e22cfd614e7fc42333784f76c7a))
* синие кнопки menu_subscription/subscription_manage_devices, исправлена индентация ([75f7245](https://github.com/cy7su/remnawave-bot/commit/75f7245078755a559e7d4881e48a564f00d83753))
* убраны действия из устройств, фикс премиум-эмодзи в кнопках, red/danger для reset_all/subscription_revoke, success для connect ([a6718c4](https://github.com/cy7su/remnawave-bot/commit/a6718c4e21e60d4214121e75950a2e8a0b2d395a))
* убраны мусорные значения deviceModel (To Be Filled By O.E.M. и т.п.) ([0aeb3d1](https://github.com/cy7su/remnawave-bot/commit/0aeb3d1b2d6f194481aabd73cdea503154c9f5f2))
* убрать 'Выберите действие:' из главного меню ([ea6a946](https://github.com/cy7su/remnawave-bot/commit/ea6a9467ba308d03d4de4121d8d7824854b3d823))
* убрать строку 'Тариф: ...' из главного меню ([9a3253d](https://github.com/cy7su/remnawave-bot/commit/9a3253d6b94da9334ece37062939661d18f2a6c7))
