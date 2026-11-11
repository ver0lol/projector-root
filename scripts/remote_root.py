#!/usr/bin/env python3
"""Use the projector's EXISTING factory qw daemon; no flash or service restart.

Default: read-only credential checks. --shell opens an explicit interactive root
session. --command executes an explicitly supplied command. These latter modes
provide real root access; the project's no-flash/no-reboot boundary still applies.
Only staging a regular copy of the existing shell is automated on the device.
"""
import argparse
import re
import shlex
import shutil
import subprocess
import sys
from project_config import load_device

MODEL = "P30_H713_V1.1_CHIHI_emcp"
QW_HASH = "022b638767ddf981ba09b8092460ea4cb837a7cdf713949ecbea7c0a0ced5759"
SH_HASH = "019086536b350b654fc37f85dcdbe4b79a1d30afc8d841b0f91691b3520c6f95"
STAGE = "/data/local/tmp/projector-root-remote-20261109"
PARENT_SHELL = STAGE + "/.agingdragonbox"
CHECK = ('id; grep -E "^(Uid|Gid|Cap|NoNewPrivs|Seccomp)" /proc/self/status; '
         'echo boot_completed=$(getprop sys.boot_completed); '
         'echo current_slot=$(getprop ro.boot.slot_suffix); '
         'echo debuggable=$(getprop ro.debuggable)')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--adb", default=shutil.which("adb"))
    p.add_argument("--serial", help="Override ADB address; identity remains pinned")
    p.add_argument("--device-config")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Read-only credential checks (default)")
    mode.add_argument("--shell", action="store_true", help="Open an interactive root shell")
    mode.add_argument("--command", help="Execute this explicitly supplied root shell command")
    args = p.parse_args()
    config = load_device(args.device_config)
    args.serial = args.serial or config['adb_serial']
    if not args.adb:
        raise RuntimeError("ADB not found")
    base = [args.adb, "-s", args.serial]

    def read(command):
        proc = subprocess.run(base + ["shell", command], capture_output=True,
                              text=True, check=True, timeout=20)
        return proc.stdout.strip()

    expected = config['expected']
    for prop, value in expected.items():
        if read("getprop " + prop) != value:
            raise RuntimeError("Different device: " + prop)
    for path, value in (("/system/bin/qw", QW_HASH), ("/system/bin/sh", SH_HASH)):
        if read("sha256sum " + shlex.quote(path)).split()[0] != value:
            raise RuntimeError("Different firmware binary: " + path)
    services = read("ps -A -o PID,ARGS")
    if not any(re.fullmatch(r"\s*\d+\s+(?:/system/bin/)?qw --daemon\s*", line)
               for line in services.splitlines()):
        raise RuntimeError("Existing factory daemon absent; refusing to start a daemon")
    if read("test -S /dev/com.koushikdutta.superuser.daemon/server && echo present") != "present":
        raise RuntimeError("Existing factory socket absent")
    # Never reuse symlinks or unexpectedly owned/writable staging directories.
    # Every device mutation below is an ordinary file under this fixed directory.
    stage = shlex.quote(STAGE)
    helper = shlex.quote(PARENT_SHELL)
    setup = (f"umask 077; if [ ! -e {stage} ]; then mkdir {stage} || exit 70; fi; "
             f"[ -d {stage} ] && [ ! -L {stage} ] || exit 71; "
             f"[ \"$(stat -c '%u:%g:%a' {stage})\" = 2000:2000:700 ] || exit 72; "
             f"if [ ! -e {helper} ]; then cp /system/bin/sh {helper} && chmod 700 {helper} || exit 73; fi; "
             f"[ -f {helper} ] && [ ! -L {helper} ] || exit 74; "
             f"sha256sum {helper}")
    if read(setup).split()[0] != SH_HASH:
        raise RuntimeError("Staged parent shell hash mismatch")
    if args.shell:
        call = "/system/bin/qw -s /system/bin/sh"
    else:
        command = args.command if args.command is not None else CHECK
        if "\0" in command:
            raise RuntimeError("NUL byte in command")
        call = "/system/bin/qw -c " + shlex.quote(command)
    # The extra statements keep the named parent alive. Do NOT replace this with
    # exec: qw's firmware-specific check reads its parent's /proc/PID/status Name.
    wrapper = call + '; result=$?; exit "$result"'
    remote = helper + " -c " + shlex.quote(wrapper)
    if args.shell:
        proc = subprocess.run(base + ["shell", "-t", remote])
    else:
        proc = subprocess.run(base + ["shell", remote], capture_output=True,
                              text=True, timeout=120)
        print(proc.stdout, end="")
        print(proc.stderr, end="", file=sys.stderr)
        if args.command is None and proc.returncode == 0:
            if not re.search(r"^Uid:\s+0\s+0\s+0\s+0\s*$", proc.stdout, re.M):
                raise RuntimeError("Root identity not confirmed")
            cap = re.search(r"^CapEff:\s+([0-9a-fA-F]+)", proc.stdout, re.M)
            if cap is None or not int(cap.group(1), 16) & (1 << 21):
                raise RuntimeError("CAP_SYS_ADMIN not confirmed")
    return proc.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print("STOP: " + str(exc), file=sys.stderr)
        sys.exit(1)
