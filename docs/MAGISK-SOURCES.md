# Magisk research — 2026-11-09

This research does not establish that the P30 / Allwinner board accepts a modified boot image. It identifies a candidate patch toolchain; boot acceptance and independent recovery remain separate requirements.

## Candidate release and trusted download

Official latest stable checked: **Magisk v31.1**, released 2026-10-25.

- Release: https://github.com/topjohnwu/Magisk/releases/tag/v31.1
- APK: https://github.com/topjohnwu/Magisk/releases/download/v31.1/Magisk-v31.1.apk
- Official asset digest: **89cefac0a318afa39fb3880c45c87b036c05bc1ebf02a7a715b7f8d7c26ac6e7**
- Digest source: https://github.com/topjohnwu/Magisk/releases/expanded_assets/v31.1

Android 7 is the minimum for v31.1; Android 11 meets that requirement. The release's default build ABIs include `armeabi-v7a` targeting `thumbv7neon-linux-androideabi`; verify the device CPU reports NEON before execution. This establishes platform eligibility, not specific board compatibility.

- ABI source: https://raw.githubusercontent.com/topjohnwu/Magisk/v31.1/build.py
- Release changelog: https://github.com/topjohnwu/Magisk/releases/tag/v31.1

## Image choice

Official installation documentation says to patch on the **same target device** and requires an unlocked bootloader. A boot image containing a usable ramdisk is the normal target; devices without boot ramdisk require a different path such as recovery. With Android 11 and this board's nonstandard firmware, inspect actual image headers and ramdisk rather than inferring from version or slot names. SELinux Permissive and world-writable eMMC nodes do not establish that the bootloader accepts modified images.

- Official install: https://topjohnwu.github.io/Magisk/install.html
- Official boot header reference: https://source.android.com/docs/core/architecture/bootloader/boot-image-header
- Lock state reference: https://source.android.com/docs/core/architecture/bootloader/locking_unlocking

## Isolated file patching on the projector

The current APK contains these needed parts:

- `assets/boot_patch.sh`
- `assets/util_functions.sh`
- `lib/armeabi-v7a/libmagisk.so` -> `magisk`
- `lib/armeabi-v7a/libmagiskboot.so` -> `magiskboot`
- `lib/armeabi-v7a/libmagiskinit.so` -> `magiskinit`
- `lib/armeabi-v7a/libinit-ld.so` -> `init-ld`

Do not copy old tutorials assuming `stub.xz` or `magisk32` is the arm32 daemon's name. The v31.1 arm32 daemon is `magisk`; `magisk32` is an additional compatibility binary extracted only for 64-bit processes on devices also supporting arm32.

Run the official file patch script in a **fresh dedicated staging directory**, with an ordinary regular file such as `boot.img` as input, never a block device or symlink to one. The script writes `stock_boot.img` and `new-boot.img` locally; its final action is repacking, with no block-device flash. It recursively chmods its current directory, so isolation matters.

Conservative candidate flags: `KEEPVERITY=true`, `KEEPFORCEENCRYPT=true`, `PATCHVBMETAFLAG=false`, `RECOVERYMODE=false`. Derive `LEGACYSAR` from the live root mount; v31.1 sets it true if the root mount source is `/dev/root`. These conservative flags intentionally preserve existing verity/encryption rather than accepting the script's false defaults.

Use boot mode on the running projector (`BOOTMODE=true`) to resolve preinit storage. `magisk --preinit-device` only prints the selected device at uid=2000; its filesystem mutation branch requires uid=0 and a nonempty MAGISKTMP. Unset `MAGISKTMP` and `MAKEDEV`, remain shell uid 2000, and do not run the daemon or installation routines.

For stricter staging isolation, source `boot_patch.sh` with `SOURCEDMODE=true` in a wrapper that defines only `ui_print`, `abort`, and `grep_prop`. Otherwise the stock util_functions sets TMPDIR=/dev/tmp and abort() tries to clean it. A wrapper can have abort simply print and exit. Require that `magiskboot cpio ramdisk.cpio test` identifies stock before this path; this avoids needing config.orig parsing for a previously patched boot. Preserve the original script and record wrapper/flags/logs in the report.

- Patch script: https://raw.githubusercontent.com/topjohnwu/Magisk/v31.1/scripts/boot_patch.sh
- Utility behavior: https://raw.githubusercontent.com/topjohnwu/Magisk/v31.1/scripts/util_functions.sh
- App extraction/invocation: https://raw.githubusercontent.com/topjohnwu/Magisk/v31.1/app/core/src/main/java/com/topjohnwu/magisk/core/tasks/MagiskInstaller.kt
- App flag detection: https://raw.githubusercontent.com/topjohnwu/Magisk/v31.1/scripts/app_functions.sh
- Preinit resolution: https://raw.githubusercontent.com/topjohnwu/Magisk/v31.1/native/src/core/mount.rs

## Local analysis / validation

`magiskboot unpack -h boot.img` reports sections and header; `magiskboot cpio ramdisk.cpio test` returns 0 for stock, 1 for Magisk, 2 for unsupported ramdisk modification. The tool preserves original image metadata and compression during repacking. `magiskboot verify` checks AVB 1.0 signatures only; it does not establish AVB 2.0 acceptance or safe boot. Verify full partition hashes before/after staging, output fits partition, kernel/DTB equality when preserving them, and Magisk ramdisk markers.

Native macOS/Linux magiskboot builds are supported by v31.1 source, but the official stable release page distributes an APK, not a host magiskboot binary. Building from verified source is possible but unnecessary if the target runs the APK's own arm32 tool in isolation. Do not use unrelated third-party host binary distributions just for convenience.

- Tool reference: https://topjohnwu.github.io/Magisk/tools.html
- Build environment: https://raw.githubusercontent.com/topjohnwu/Magisk/v31.1/docs/build.md
- Host compile path: https://raw.githubusercontent.com/topjohnwu/Magisk/v31.1/native/src/boot/build.rs

## Installation blocker

A valid candidate image is not permission to install. Until USB/physical independent recovery and exact original restore images are verified, network ADB is not a recovery route for a boot failure. No bootloader unlock, flash, eMMC writes, slot switch, reboot, or AVB disable is justified by this research alone.
