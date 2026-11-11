# projector-root

Исследование Android-проектора **P30 / Allwinner H713**: доступ к уже работающей заводской root-службе, чтение GPT/boot и проверка Android Verified Boot. Домашний экран — отдельный [focal-launcher](https://github.com/ver0lol/focal-launcher).

**Это исследовательские инструменты, не прошивка. Magisk не установлен; независимое аварийное восстановление не подтверждено.**

## Начать без проектора

Нужны Python 3.9+ и Git. Из корня репозитория:

```sh
python3 scripts/check_repo.py
python3 -m unittest discover -s scripts/qa -p 'test_*.py'
```

Проверки полностью офлайн: без ADB, сети, root и образов. При наличии Make то же делает `make check`.

На Linux/macOS используйте `python3`. В Windows для офлайн-проверок можно заменить его на `py -3`; работа с физическим устройством в Windows отдельно не проверялась. Для Linux-окружения есть [WSL 2](https://learn.microsoft.com/windows/wsl/install). SDK, Magisk и firmware backup для чтения кода и публикации не нужны.

## Устройство

Наблюдения исходного исследования 9–10 ноября 2026 года. Торговое название и маркировка физической платы отдельно не подтверждены.

| Параметр | Наблюдение |
| --- | --- |
| OEM | `P30_H713_V1.1_CHIHI_emcp` |
| Платформа | Allwinner `sun50iw12p1` / `ares`, `QuadCore-H713M`, 4 ядра |
| Система | Android 11 / API 30, Linux 5.4.99, `armeabi-v7a` (ARM32, NEON) |
| Прошивка | `202410161629`, `OB.283`, boot device `h713m-tuna_p3`, build `hxws10161629` |
| Память | `MemTotal: 1474000 kB` (около 1,41 ГиБ); eMMC 7 818 182 656 байт |
| Геометрия | 15 269 888 секторов по 512 байт |
| Экран Android | 1280 × 720, 160 dpi; физическая LCD-матрица отдельно не проверена |
| Доступ | ADB shell UID 2000, SELinux Permissive; factory `qw` даёт UID/GID 0, CapEff `0000003fffffffff` |
| Загрузка | Активен `_a`; сохранённые разделы `_b` заполнены нулями |
| AVB | `ro.boot.vbmeta.device_state=locked`; фактическая политика bootloader не установлена |

<img src="https://technoshop.pk/wp-content/uploads/2024/04/P30-Android-Projector.webp" alt="Пример корпуса P30 из каталога Techno Shop" width="280">

Фото — внешний пример [P30 с H713 из каталога Techno Shop](https://technoshop.pk/product/p30-mini-4k-android-portable-projector/). **Совпадение корпуса и ревизии с исследованным устройством не подтверждено.** Права остаются у правообладателя фото; файл не включён в репозиторий. Generic model/fingerprint из Android не заменяет OEM-идентичность.

## Подключение к своему устройству

Публичные исходники не содержат адреса ADB и серийного номера. Для осознанной диагностики создайте игнорируемый `local/device.json` по образцу `config/device.example.json`, указав **заранее проверенную** идентичность. Не переносите настройки от похожего проектора и не обучайте guard на первом подключённом устройстве.

Владелец с PRIVATE kit от focal-launcher может использовать его проверенный `local/device.json` и здесь. Ключ подписи для root-диагностики не нужен. [ADB](https://developer.android.com/tools/releases/platform-tools) устанавливается отдельно; компьютер должен видеть проектор в локальной сети.

Сначала прочитайте справку — без подключения:

```sh
python3 scripts/remote_root.py --help
python3 scripts/device_backup.py --help
```

Только в рамках явной задачи на проверенном устройстве:

```sh
python3 scripts/remote_root.py --check
```

`--check` сверяет идентичность, хеши `sh`/`qw`, существующий daemon и сокет; при необходимости создаёт обычную копию штатного shell в фиксированном `/data/local/tmp`. Это действие на работающем проекторе, не офлайн-тест. Если daemon отсутствует, скрипт останавливается. Явные `--shell` и `--command` не отменяют ограничений ниже.

## Как найден factory root

Штатный init уже запускал `/system/bin/qw --daemon` с root; сокет — `/dev/com.koushikdutta.superuser.daemon/server`. Клиент проверяет имя родителя и допускает `.agingdragonbox`, `inner.dragonbox`, `ech.dragonenter`. Копия **неизменённого** `/system/bin/sh` с первым именем позволяет обратиться к существующей службе. Бинарник `qw` не патчился, новый daemon не запускался.

| Оригинальный файл | SHA-256 |
| --- | --- |
| `/system/bin/qw` | `022b638767ddf981ba09b8092460ea4cb837a7cdf713949ecbea7c0a0ced5759` |
| `/system/bin/sh` | `019086536b350b654fc37f85dcdbe4b79a1d30afc8d841b0f91691b3520c6f95` |

При исходной проверке ADB остался shell-процессом, boot_a/boot_b не изменились, служба `qw` сохранила основной процесс. Обычный `su` давал ограниченные real UID/capabilities и не считался полным root. Результат относится к конкретной прошивке, не ко всем H713.

## Инструменты и образы

| Файл | Назначение |
| --- | --- |
| `scripts/remote_root.py` | Проверка и явный доступ к уже работающему factory `qw` |
| `scripts/device_backup.py` | Чтение родительского eMMC через GPT, CRC, границы и хеши |
| `scripts/inspect_images.py` | Локальная проверка заголовков, размеров и SHA-256 |
| `scripts/verify_avb_constraints.py` | Локальная проверка собственных сохранённых AVB-образов |
| `scripts/prepare_magisk.py` | Изолированная подготовка кандидата; не установка и не прошивка |
| `scripts/project_config.py` | Отказ без конфигурации, с шаблоном или при несовпадении устройства |
| `vendor/avbtool.py` | Неизменённый AOSP-инструмент с исходной лицензией |

Пример для **уже имеющегося своего** образа:

```sh
python3 scripts/inspect_images.py backups/boot_a.img
```

Образы, отчёты, Magisk APK и vendor-бинарники в Git не входят. В исходном исследовании были сохранены 18 образов общим размером 328 204 288 байт и обе GPT; полная eMMC, super и пользовательские данные не копировались. Участок до первого GPT-раздела (36 МиБ) — raw-копия, не заводской образ PhoenixSuit/FEL. Сами backup здесь отсутствуют.

## AVB и Magisk: результаты

Оба boot читались полностью дважды; SHA-256 совпал на проекторе и компьютере. Основная и резервная GPT прошли CRC32 и сверку геометрии; необычное `reserved=1` зафиксировано отдельно. Подписи vbmeta_a/system/vendor встроенными ключами проверены, общий digest совпал с работающей системой. Это не доказывает доверие аппаратного загрузчика к ключу.

| Образ | SHA-256 |
| --- | --- |
| Исходный boot_a | `d4f7c4ebe5dc2d9fc05d20db9bbfe95d3a1dfb62417bb73538e48cabd76fcb70` |
| Нулевой boot_b | `3b6a07d0d404fab4e23b6d34bc6696a6a312dd92821332385e5af7c01c421351` |
| Неустановленный кандидат | `27c837551d8da3c247263ffaa411d13db431eabc4f11f942e3b296086e1c2865` |

Исходный boot_a — header v3, раздел 67 108 864 байта; ядро 21 970 604 байта, ramdisk 13 844 960 байт. В кандидате ядро неизменно, ramdisk 14 090 479 байт, но кандидат **не совпадает с подписанным stock boot hash**. При применении AVB загрузчик может его отклонить. Загрузка кандидата не проверялась.

Использовался [официальный Magisk 31.1](https://github.com/topjohnwu/Magisk/releases/tag/v31.1), APK SHA-256 `89cefac0a318afa39fb3880c45c87b036c05bc1ebf02a7a715b7f8d7c26ac6e7`. Скрипт закреплён за ним. Флаги кандидата: `KEEPVERITY=true`, `KEEPFORCEENCRYPT=true`, `PATCHVBMETAFLAG=false`, `RECOVERYMODE=false`, `LEGACYSAR=false`, `PREINITDEVICE=metadata`.

Подготовка работает с обычным файлом в отдельном staging-каталоге. Штатный `boot_patch.sh` сохраняется; оболочка изолирует вывод и аварийное завершение от `/dev/tmp`. Корректный кандидат не даёт разрешения на установку Magisk или запись boot.

## Границы действий и восстановление

**Пока независимое восстановление не доказано и владелец не разрешил конкретное действие, запрещены:** запись raw-разделов, flash/erase/unlock, изменение AVB/boot-control, переключение слотов, reboot, вход в fastboot/FEL/recovery, установка root и запуск/перезапуск root daemon. Общего «продолжай» недостаточно. Пустой слот B не является запасной системой.

Не читайте Allwinner sysfs-узлы `usb_device` / `usb_host` и не меняйте `persist.sys.usb0device`: чтение trigger-узла меняет USB-роль. Наличие UDC в device tree не доказывает доступность сервисного порта. Не соединяйте непроверенный USB host-порт с компьютером кабелем USB-A ↔ USB-A.

До будущей записи нужны подтверждённая плата, независимые от Android порт/кабель/питание и сервисный маршрут, совместимый инструмент с проверенной геометрией и возможность вернуть собственный оригинал. Сетевой ADB после неудачной загрузки не является восстановлением. Unlock может стереть данные; raw-дамп не заменяет заводскую прошивку.

Историческая геометрия boot_a: `/dev/block/mmcblk0p5`, секторы **205824–336895**, смещение **105381888**, размер **67108864** байта. Если менялся только boot_a, предполагаемая цель возврата — только собственный проверенный boot_a. После изменений vbmeta/bootloader/metadata этого недостаточно. Готовой проверенной процедуры записи здесь нет.

Сохраняйте оригиналы неизменными; проверяйте геометрию, GPT CRC, границы, длину и SHA-256. `adb exec-out` смешивает удалённый stderr с stdout: вывод `dd` не должен попасть в бинарные данные. Не добавляйте запись в backup-скрипты, fallback запуска daemon, remount или ослабление guard.

## Разработка и происхождение

Вся документация находится здесь. Перед коммитом выполняйте офлайн-проверки из начала файла. Публикация не требует подключения к проектору. Не используйте `git add -f` для приватных данных и `git clean -fdx` / `git clean -X`: игнорируемые каталоги могут содержать единственные копии данных. Отличайте наблюдения от гипотез.

Автор и коммитер локальной истории — **Vlad Remizov**; имя/email настроены только для этого репозитория. Дальше используйте обычные `git add`, `git commit`, `git push`; опубликованную историю не переписывайте. `local/`, `backups/`, `artifacts/`, `evidence/`, `tools/`, APK, ключи и исходные ZIP исключены из Git.

Проект владельца создавался с помощью ИИ и друга. Авторство upstream сохраняется:

- **AOSP:** `vendor/avbtool.py`, Copyright 2016 The Android Open Source Project. Полная лицензия MIT и отказ от гарантий сохранены в заголовке. SHA-256 `e5a664a38db623da00f080219bc0ee60a640a9dc4a872803616fae4938ac749b`, проверяется офлайн. [Проект AVB](https://android.googlesource.com/platform/external/avb/).
- **Magisk / topjohnwu:** внешний инструмент, его APK не распространяется здесь. [Установка](https://topjohnwu.github.io/Magisk/install.html), [инструменты](https://topjohnwu.github.io/Magisk/tools.html).
- **Koushikdutta Superuser:** [daemon/client архитектура](https://github.com/koush/Superuser/blob/master/Superuser/jni/su/su.c); OEM-проверка имён установлена отдельно по бинарнику этой прошивки. Vendor-бинарник не распространяется.
- **Android:** [AVB](https://android.googlesource.com/platform/external/avb/+/master/README.md), [A/B](https://source.android.com/docs/core/ota/ab/ab_implement), [блокировка загрузчика](https://source.android.com/docs/core/architecture/bootloader/locking_unlocking). Общая документация не доказывает совместимость конкретной платы.
