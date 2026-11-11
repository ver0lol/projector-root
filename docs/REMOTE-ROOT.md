# Root через Wi-Fi без прошивки

9 ноября 2026 года на этом P30 подтверждён полный root через уже работающую заводскую службу `qw`. Прошивка, перезагрузка, изменение boot/vbmeta, свойств Android, роли USB и перезапуск ADB не потребовались. Magisk не устанавливался.

## Подтверждённый результат

```text
uid=0(root) gid=0(root) groups=0(root) context=u:r:qw:s0
Uid:    0 0 0 0
Gid:    0 0 0 0
CapPrm: 0000003fffffffff
CapEff: 0000003fffffffff
CapBnd: 0000003fffffffff
```

После проверки SHA-256 boot_a и boot_b совпали с оригиналами. Основной процесс ADB сохранил PID 2518 и UID shell; `ro.debuggable=0`, текущий слот `_a`, Android работает. Заводская служба `qw --daemon` сохранила основной PID 2457. Создавались лишь её кратковременные дочерние процессы для диагностики.

## Повторение

Из корня проекта:

```sh
python3 scripts/remote_root.py --check
python3 scripts/remote_root.py --shell
python3 scripts/remote_root.py --command 'id'
```

Первый вариант только проверяет права. Второй открывает интерактивную root оболочку; третий выполняет указанную команду. Ограничение владельца на прошивку, запись разделов и reboot продолжает действовать. Сам скрипт не запускает daemon, не меняет свойства, не устанавливает приложение и не содержит операции прошивки.

Скрипт закреплён за серийным номером `DEVICE_SERIAL_FROM_LOCAL_CONFIG` и этой сборкой. Перед выполнением он сверяет модель, платформу, хеши штатных бинарников, наличие работающего daemon и его сокета. Если daemon отсутствует, выполнение прекращается.

## Как устроен доступ

`/system/etc/init/qw.rc` уже запускает `/system/bin/qw --daemon` как root при штатной загрузке. Daemon слушает локальный Unix socket `/dev/com.koushikdutta.superuser.daemon/server`. Его бинарник содержит код Koushikdutta Superuser. Клиентский вход до разбора аргументов проверяет имя родительского процесса через `/proc/PID/status` и разрешает три заводских имени: `.agingdragonbox`, `inner.dragonbox`, `ech.dragonenter`.

При обычном вызове из ADB имя родителя не проходит эту проверку. Копия **неизменённого** штатного `/system/bin/sh`, сохранённая как `/data/local/tmp/projector-root-remote-20261109/.agingdragonbox`, даёт допустимое имя процесса. Эта оболочка вызывает уже работающий qw с диагностической командой, оставаясь её родителем. Затем factory daemon создаёт процесс с полным root.

Исходный qw не патчился. Хеши:

| Файл | SHA-256 |
| --- | --- |
| /system/bin/qw | 022b638767ddf981ba09b8092460ea4cb837a7cdf713949ecbea7c0a0ced5759 |
| /system/bin/sh и его временная копия | 019086536b350b654fc37f85dcdbe4b79a1d30afc8d841b0f91691b3520c6f95 |

Это механизм доступа к уже имеющейся фабричной службе, а не установленная Magisk среда. Выдача прав произвольным Android приложениям через Magisk Manager не настраивалась. Поведение после reboot не проверялось, поскольку reboot запрещён.

## Почему обычный su не подошёл

Вызов `/system/xbin/su 0` не может сменить группу на root. Вариант `su 0,2000` сохраняет группу shell и даёт effective UID 0, но real UID остаётся 2000, а CapEff равен нулю. Обычный sh затем сбрасывает effective UID; `sh -p` его сохраняет, однако не возвращает отсутствующие capabilities. Это ограниченный путь, и его нельзя считать полным root. Полный результат выше получен через отдельную factory службу, унаследовавшую права от init.

## Проверка и источники

- Фактические журналы и проверка хешей: `evidence/remote-root-20261109/full-root-verification.txt`.
- Локально разобранные оригиналы и извлечённые debug symbols: `tools/vendor-analysis/`.
- Основные адреса qw: `main=0x1e850`, `su_main=0x1e858`, `getNameByPid=0x1e5b8`, `connect_daemon=0x1ff24`. Проверка имён находится в диапазоне `0x1e890–0x1e8d6`. Эти адреса относятся только к указанному SHA-256.
- [Исходный Koushikdutta Superuser](https://github.com/koush/Superuser/blob/master/Superuser/jni/su/su.c) объясняет daemon/client архитектуру; OEM проверка имён подтверждена отдельно анализом бинарника этого проектора.
- [AOSP о root и unroot ADB](https://android.googlesource.com/platform/packages/modules/adb/+/HEAD/docs/dev/root.md): смена режима ADB требует перезапуска daemon; этот путь не использовался.
- [Android Intent API](https://developer.android.com/reference/android/content/Intent#CATEGORY_HOME): собственный домашний экран является launcher Activity.
