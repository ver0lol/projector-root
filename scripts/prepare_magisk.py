#!/usr/bin/env python3
"""Prepare a Magisk candidate as shell uid 2000; never flash, install or reboot.

All device writes are regular files inside one fresh /data/local/tmp staging
folder. Block-device access is read-only and used solely to confirm provenance.
The verified official boot_patch.sh is sourced unchanged; a tiny helper wrapper
avoids util_functions.sh's global /dev/tmp cleanup behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from project_config import load_device

ROOT = Path(__file__).resolve().parents[1]
APK_SHA256 = "89cefac0a318afa39fb3880c45c87b036c05bc1ebf02a7a715b7f8d7c26ac6e7"
APK_NAME = "Magisk-v31.1.apk"
FILES = {
    "assets/boot_patch.sh": "boot_patch.sh",
    "assets/util_functions.sh": "util_functions.sh",
    "lib/armeabi-v7a/libmagisk.so": "magisk",
    "lib/armeabi-v7a/libmagiskboot.so": "magiskboot",
    "lib/armeabi-v7a/libmagiskinit.so": "magiskinit",
    "lib/armeabi-v7a/libinit-ld.so": "init-ld",
}
PARTS = {"boot_a": "mmcblk0p5", "boot_b": "mmcblk0p6"}


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def boot_header(path: Path) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(4096)
    require(raw[:8] == b"ANDROID!", "Expected an Android boot image at byte zero")
    version = struct.unpack_from("<I", raw, 40)[0]
    require(version in (0, 1, 2, 3, 4), f"Unsupported boot header version {version}")
    kernel_size = struct.unpack_from("<I", raw, 8)[0]
    ramdisk_size = struct.unpack_from("<I", raw, 12 if version >= 3 else 16)[0]
    page_size = 4096 if version >= 3 else struct.unpack_from("<I", raw, 36)[0]
    require(kernel_size > 0, "Image has no kernel")
    require(ramdisk_size > 0, "boot has no ramdisk: investigate vendor_boot before patching")
    require(page_size in (2048, 4096, 8192, 16384, 32768, 65536), "Invalid page size")
    return {"header_version": version, "kernel_size": kernel_size,
            "ramdisk_size": ramdisk_size, "page_size": page_size}


class Session:
    def __init__(self, adb: str, serial: str, output: Path):
        self.adb = adb
        self.serial = serial
        self.output = output
        self.log = output / "operations.jsonl"

    def run(self, args: list[str], *, timeout: int = 300,
            expected: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
        proc = subprocess.run([self.adb, "-s", self.serial, *args],
                              capture_output=True, timeout=timeout)
        entry = {"utc": datetime.now(timezone.utc).isoformat(), "args": args,
                 "returncode": proc.returncode,
                 "stdout": proc.stdout.decode("utf-8", "replace"),
                 "stderr": proc.stderr.decode("utf-8", "replace")}
        with self.log.open("a", encoding="utf-8") as out:
            out.write(json.dumps(entry, ensure_ascii=False) + "\n")
        require(proc.returncode in expected,
                f"ADB operation failed ({proc.returncode}): {args!r}\n" + entry["stderr"] + entry["stdout"])
        return proc

    def shell(self, source: str, *, expected: tuple[int, ...] = (0,)) -> str:
        return self.run(["shell", source], expected=expected).stdout.decode("utf-8", "replace").strip()

    def shell_log(self, source: str) -> str:
        proc = self.run(["shell", source])
        return (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()

    def pull(self, remote: str, local: Path) -> None:
        self.run(["pull", remote, str(local)])


def wrapper(remote: str) -> str:
    # The official patch script intentionally probes commands with nonzero exit
    # statuses. Do not set -e/-u or alter its code. Keep helper cleanup confined.
    return f'''#!/system/bin/sh
cd {shlex.quote(remote)} || exit 70
[ "$(pwd -P)" = {shlex.quote(remote)} ] || exit 71
[ "$(id -u)" = 2000 ] || exit 72
[ -f stock-input.img ] && [ ! -L stock-input.img ] || exit 73
unset MAGISKTMP MAKEDEV PREINITDEVICE BOOTNAND MODPATH TMPDIR OUTFD
export BOOTMODE=true SOURCEDMODE=true
export KEEPVERITY=true KEEPFORCEENCRYPT=true PATCHVBMETAFLAG=false
export RECOVERYMODE=false
LEGACYSAR=false
if grep ' / ' /proc/mounts | grep -q '/dev/root'; then LEGACYSAR=true; fi
export LEGACYSAR
ui_print() {{ printf '%s\\n' "$1"; }}
abort() {{ printf '%s\\n' "$1" >&2; exit 74; }}
grep_prop() {{ local key="$1"; shift; sed -n "s/^${{key}}=//p" "$@" | head -n 1; }}
printf 'BOOTMODE=%s\\nLEGACYSAR=%s\\nKEEPVERITY=%s\\nKEEPFORCEENCRYPT=%s\\nPATCHVBMETAFLAG=%s\\nRECOVERYMODE=%s\\n' "$BOOTMODE" "$LEGACYSAR" "$KEEPVERITY" "$KEEPFORCEENCRYPT" "$PATCHVBMETAFLAG" "$RECOVERYMODE" > patch-flags.txt
set -- ./stock-input.img
. ./boot_patch.sh "$@"
result=$?
[ "$result" = 0 ] || exit "$result"
[ -f new-boot.img ] && [ ! -L new-boot.img ] || exit 75
exit 0
'''


def live_partition_hash(session: Session, part: str, expected_size: int) -> tuple[str, dict]:
    node = PARTS[part]
    values = session.shell(f"cat /sys/class/block/{node}/start /sys/class/block/{node}/size").split()
    require(len(values) == 2 and all(re.fullmatch(r"[0-9]+", v) for v in values),
            "Unable to verify partition start and length")
    start, count = map(int, values)
    require(count * 512 == expected_size, "Backup size differs from live partition size")
    require(start > 0 and count > 0, "Invalid partition geometry")
    # Only 'if=' exists. Hash must match a non-empty exact-sized local image.
    digest = session.shell(
        f"dd if=/dev/block/mmcblk0 bs=512 skip={start} count={count} 2>/dev/null | toybox sha256sum").split()[0]
    require(re.fullmatch(r"[0-9a-f]{64}", digest) is not None, "Malformed live partition SHA256")
    return digest, {"node": node, "start_sector": start, "sector_count": count}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot", type=Path, default=ROOT / "backups" / "boot_a.img")
    parser.add_argument("--apk", type=Path, default=ROOT / "tools" / APK_NAME)
    parser.add_argument("--partition", choices=tuple(PARTS), default="boot_a")
    parser.add_argument("--serial")
    parser.add_argument("--device-config")
    parser.add_argument("--adb", default=shutil.which("adb") or "adb")
    parser.add_argument("--extract-only", action="store_true",
                        help="Validate/extract local toolchain without contacting the device")
    args = parser.parse_args()
    config = None if args.extract_only else load_device(args.device_config)
    if config: args.serial = args.serial or config['adb_serial']
    require(args.boot.is_file() and not args.boot.is_symlink(), "Backup must be a regular file, not symlink")
    require(args.apk.is_file() and not args.apk.is_symlink(), "APK must be a regular file, not symlink")
    source_hash = sha256(args.boot)
    require(sha256(args.apk) == APK_SHA256, "APK does not match official v31.1 SHA256")
    header = boot_header(args.boot)
    identity = hashlib.sha256((source_hash + APK_SHA256 + uuid.uuid4().hex).encode()).hexdigest()[:20]
    out = ROOT / "artifacts" / f"magisk-v31.1-{args.partition}-{identity}"
    out.mkdir(parents=True, exist_ok=False)
    local = out / "staging"
    local.mkdir()
    report = {"status": "preparing", "source": str(args.boot.resolve()),
              "source_sha256": source_hash, "source_size": args.boot.stat().st_size,
              "apk_sha256": APK_SHA256, "apk": str(args.apk.resolve()), "boot_header": header,
              "partition": args.partition, "serial": args.serial,
              "installation_authorized": False, "device_block_writes": False,
              "stage_files_only": True, "remote_directory": None}
    report_file = out / "report.json"
    report_file.write_text(json.dumps(report, indent=2) + "\n")
    try:
        with zipfile.ZipFile(args.apk) as archive:
            require(archive.testzip() is None, "APK zip integrity failure")
            for member, name in FILES.items():
                data = archive.read(member)
                (local / name).write_bytes(data)
                os.chmod(local / name, 0o755)
        report["tool_sha256"] = {name: sha256(local / name) for name in FILES.values()}
        # Explicit regular local copy: do not propagate symlinks.
        shutil.copyfile(args.boot, local / "stock-input.img")
        remote = f"/data/local/tmp/projector-root-{identity}"
        (local / "patch-file-only.sh").write_text(wrapper(remote))
        os.chmod(local / "patch-file-only.sh", 0o755)
        if args.extract_only:
            report["status"] = "local-toolchain-ready-device-not-contacted"
            print(out)
            return
        session = Session(args.adb, args.serial, out)
        require(session.shell("id -u") == "2000", "Only ordinary ADB shell uid 2000 is allowed")
        require(session.shell("getprop ro.build.version.sdk") == "30", "Expected Android 11 / API 30")
        require(session.shell("getprop ro.product.cpu.abi") == "armeabi-v7a", "Unexpected target ABI")
        require(session.shell("getprop ro.hardware") == "sun50iw12p1", "Unexpected target hardware")
        require(session.shell("getprop ro.board.platform") == "ares", "Unexpected target board")
        require(session.shell("getprop ro.serialno") == config['expected']['ro.serialno'], "Unexpected target serial")
        require(session.shell("getprop oem.product.model") == "P30_H713_V1.1_CHIHI_emcp", "Unexpected OEM model")
        require(session.shell("getprop ro.boot.slot_suffix") == "_" + args.partition[-1],
                "Requested source is not the currently running slot")
        require("neon" in session.shell("cat /proc/cpuinfo").split(), "NEON CPU feature not confirmed")
        live_before, geometry = live_partition_hash(session, args.partition, report["source_size"])
        require(live_before == source_hash, "Source backup does not match the running device's partition")
        report["live_partition_sha256_before"] = live_before
        report["partition_geometry"] = geometry
        # Fresh directory only. Existing paths are never reused or recursively removed.
        session.shell(f"umask 077; mkdir {shlex.quote(remote)}")
        report["remote_directory"] = remote
        for path in local.iterdir():
            require(path.is_file() and not path.is_symlink(), "Unexpected staging file type")
            session.run(["push", str(path), remote + "/" + path.name])
        for path in local.iterdir():
            got = session.shell("toybox sha256sum " + shlex.quote(remote + "/" + path.name)).split()[0]
            require(got == sha256(path), f"Pushed file hash mismatch: {path.name}")
        qremote = shlex.quote(remote)
        session.shell(f"cd {qremote} && chmod 755 magisk magiskboot magiskinit init-ld patch-file-only.sh && mkdir analysis-original analysis-patched")
        # Inspect stock separately, without altering the official patch working dir.
        original_log = session.shell_log(f"cd {qremote}/analysis-original && ../magiskboot unpack -h ../stock-input.img")
        (out / "stock-unpack.txt").write_text(original_log + "\n")
        session.shell(f"test -f {qremote}/analysis-original/ramdisk.cpio")
        session.shell(f"cd {qremote}/analysis-original && ../magiskboot cpio ramdisk.cpio test")
        session.pull(remote + "/analysis-original", out / "original")
        patch_log = session.shell_log(f"cd {qremote} && /system/bin/sh ./patch-file-only.sh")
        (out / "patch-log.txt").write_text(patch_log + "\n")
        session.pull(remote + "/patch-flags.txt", out / "patch-flags.txt")
        session.pull(remote + "/new-boot.img", out / "magisk-patched-candidate.img")
        pushed_source_hash = session.shell(f"toybox sha256sum {qremote}/stock-input.img").split()[0]
        require(pushed_source_hash == source_hash, "Staged source changed during patching")
        candidate = out / "magisk-patched-candidate.img"
        require(0 < candidate.stat().st_size <= args.boot.stat().st_size,
                "Candidate is empty or larger than its partition")
        require(sha256(candidate) != source_hash, "Candidate is unchanged")
        report["candidate_header"] = boot_header(candidate)
        patched_log = session.shell_log(f"cd {qremote}/analysis-patched && ../magiskboot unpack -h ../new-boot.img")
        (out / "patched-unpack.txt").write_text(patched_log + "\n")
        # cpio test deliberately uses exit code 1 to indicate Magisk.
        session.shell(f"cd {qremote}/analysis-patched && ../magiskboot cpio ramdisk.cpio test", expected=(1,))
        for item in ("init", "overlay.d/sbin/magisk.xz", "overlay.d/sbin/init-ld.xz", ".backup/.magisk"):
            session.shell(f"cd {qremote}/analysis-patched && ../magiskboot cpio ramdisk.cpio " + shlex.quote("exists " + item))
        listing = session.shell_log(f"cd {qremote}/analysis-patched && ../magiskboot cpio ramdisk.cpio 'ls -r'")
        (out / "patched-ramdisk-list.txt").write_text(listing + "\n")
        session.shell(f"cd {qremote}/analysis-patched && ../magiskboot cpio ramdisk.cpio 'extract .backup/.magisk magisk-config.txt' 'extract init injected-init'")
        session.shell(f"cd {qremote}/analysis-patched && chmod 644 magisk-config.txt injected-init")
        session.pull(remote + "/analysis-patched", out / "patched")
        config = (out / "patched" / "magisk-config.txt").read_text()
        require({"KEEPVERITY=true", "KEEPFORCEENCRYPT=true"}.issubset(set(config.splitlines())),
                "Candidate did not preserve verity and encryption flags")
        require(sha256(out / "patched" / "injected-init") == sha256(local / "magiskinit"),
                "Patched init is not the verified official magiskinit")
        comparison = {}
        for name in ("kernel", "kernel_dtb", "dtb", "extra", "second", "recovery_dtbo"):
            before, after = out / "original" / name, out / "patched" / name
            require(before.exists() == after.exists(), f"Image section presence changed: {name}")
            if before.exists():
                comparison[name] = {"original_sha256": sha256(before), "patched_sha256": sha256(after)}
                require(comparison[name]["original_sha256"] == comparison[name]["patched_sha256"],
                        f"Candidate unexpectedly changes {name}; retained for investigation only")
        live_after, _ = live_partition_hash(session, args.partition, report["source_size"])
        report["live_partition_sha256_after"] = live_after
        require(live_after == source_hash, "Live partition changed; candidate is not approved")
        require(sha256(args.boot) == source_hash, "Local original changed")
        report.update(status="candidate-prepared-not-installed", candidate=str(candidate),
                      candidate_size=candidate.stat().st_size, candidate_sha256=sha256(candidate),
                      unchanged_sections=comparison, staged_source_sha256_after=pushed_source_hash,
                      ramdisk_status="Magisk (test exit 1)",
                      recovery_verified=False, safe_to_flash=False)
        print(json.dumps({"status": report["status"], "artifact_directory": str(out),
                          "candidate_sha256": report["candidate_sha256"], "safe_to_flash": False}, indent=2))
    except BaseException as exc:
        report.update(status="failed-candidate-not-approved", error=str(exc), safe_to_flash=False)
        raise
    finally:
        report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"Preparation stopped: {exc}", file=sys.stderr)
        sys.exit(1)
