#!/usr/bin/env python3
"""Read-only ADB inventory and independently verified raw eMMC backups.

This tool contains no device write, reboot, slot-change, unlock or flash action.
All raw input is the parent eMMC device, never an assumed partition offset.
"""

import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from project_config import load_device

ROOT = Path(__file__).resolve().parents[1]
SECTOR = 512
RAW_DEVICE = "/dev/block/mmcblk0"
EXPECTED_MODEL = "P30_H713_V1.1_CHIHI_emcp"
EXPECTED_HARDWARE = "sun50iw12p1"


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def decode_header(data, expected_lba, sectors):
    if len(data) != SECTOR or data[:8] != b"EFI PART":
        raise ValueError("Missing GPT header signature")
    revision, size, expected_crc, reserved = struct.unpack_from("<IIII", data, 8)
    # This exact vendor image uses reserved=1 despite the UEFI definition of 0.
    # Retain that observed deviation in evidence, with all CRC/bounds enforced.
    if revision != 0x10000 or not 92 <= size <= SECTOR or reserved not in (0, 1):
        raise ValueError("Unsupported/malformed GPT header")
    check = bytearray(data[:size])
    check[16:20] = b"\0" * 4
    if zlib.crc32(check) & 0xFFFFFFFF != expected_crc:
        raise ValueError("GPT header CRC32 mismatch")
    current, alternate, first, last = struct.unpack_from("<QQQQ", data, 24)
    table_lba, count, entry_size, table_crc = struct.unpack_from("<QIII", data, 72)
    if current != expected_lba or alternate not in (1, sectors - 1):
        raise ValueError("GPT current/alternate header position mismatch")
    if not 2 <= first <= last < sectors - 1:
        raise ValueError("GPT usable bounds invalid")
    if not 1 <= count <= 4096 or not 128 <= entry_size <= 4096 or entry_size % 8:
        raise ValueError("GPT table dimensions invalid")
    table_bytes = count * entry_size
    if not 2 <= table_lba < sectors or table_lba * SECTOR + table_bytes > sectors * SECTOR:
        raise ValueError("GPT entry table outside eMMC")
    table_last = table_lba + (table_bytes + SECTOR - 1) // SECTOR - 1
    if expected_lba == 1 and table_last >= first:
        raise ValueError("Primary GPT entry table overlaps usable space")
    if expected_lba == sectors - 1 and not last < table_lba <= table_last < expected_lba:
        raise ValueError("Backup GPT entry table overlaps usable space/header")
    return dict(current_lba=current, alternate_lba=alternate, first_usable=first,
                last_usable=last, disk_guid=data[56:72].hex(), table_lba=table_lba,
                entry_count=count, entry_size=entry_size, table_crc32=table_crc,
                header_crc32=expected_crc, reserved_field=reserved, table_bytes=table_bytes)


def decode_entries(data, header):
    if len(data) != header["table_bytes"] or zlib.crc32(data) & 0xFFFFFFFF != header["table_crc32"]:
        raise ValueError("GPT entry table CRC32/length mismatch")
    entries = []
    names = set()
    for i in range(header["entry_count"]):
        e = data[i * header["entry_size"]:(i + 1) * header["entry_size"]]
        if e[:16] == b"\0" * 16:
            continue
        start, end, attrs = struct.unpack_from("<QQQ", e, 32)
        name = e[56:128].decode("utf-16le").rstrip("\0")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in names:
            raise ValueError("Unsafe/duplicate GPT partition name")
        if not header["first_usable"] <= start <= end <= header["last_usable"]:
            raise ValueError("Partition outside GPT usable bounds")
        entry = dict(index=i + 1, name=name, start_sector=start, end_sector=end,
                     sectors=end - start + 1, size_bytes=(end - start + 1) * SECTOR,
                     attributes=attrs, unique_guid=e[16:32].hex())
        for previous in entries:
            if start <= previous["end_sector"] and end >= previous["start_sector"]:
                raise ValueError("Overlapping GPT partition ranges")
        entries.append(entry)
        names.add(name)
    return entries


class Reader:
    def __init__(self, args):
        self.args = args
        self.config = load_device(args.device_config)
        args.serial = args.serial or self.config['adb_serial']
        self.adb = shutil.which(args.adb)
        if not self.adb:
            raise ValueError("ADB executable not found")
        self.base = [self.adb, "-s", args.serial]
        self.root = Path(args.output).resolve()
        self.evidence = self.root / "evidence"
        self.backups = self.root / "backups"
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.backups.mkdir(parents=True, exist_ok=True)
        self.sectors = None

    def shell(self, command, timeout=60):
        result = subprocess.run(self.base + ["shell", command], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=True)
        return result.stdout.decode("utf-8", "replace").replace("\r\n", "\n")

    def guard(self):
        subprocess.run(self.base + ["get-state"], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=15, check=True)
        observed = {}
        for prop, expected in self.config['expected'].items():
            actual = self.shell("getprop " + prop).strip()
            observed[prop] = actual
            if actual != expected:
                raise ValueError("Refusing a different device: {}={!r}".format(prop, actual))
        size = self.shell("cat /sys/class/block/mmcblk0/size").strip()
        if not size.isdigit() or not 1024 < int(size) < 2 ** 31:
            raise ValueError("Invalid eMMC sector count")
        self.sectors = int(size)
        return observed

    def dd(self, offset, length):
        if self.sectors is None or offset < 0 or length <= 0 or offset + length > self.sectors * SECTOR:
            raise ValueError("Raw read outside known eMMC bounds")
        if offset % SECTOR or length % SECTOR:
            raise ValueError("Raw reads must be sector aligned")
        block = math.gcd(math.gcd(offset, length), 1024 * 1024)
        return "dd if={} bs={} skip={} count={} 2>/dev/null".format(
            RAW_DEVICE, block, offset // block, length // block)

    def read_bytes(self, offset, length):
        p = subprocess.run(self.base + ["exec-out", self.dd(offset, length)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=180, check=True)
        if len(p.stdout) != length:
            raise ValueError("Short/contaminated raw read: {} != {}".format(len(p.stdout), length))
        return p.stdout

    def gpt(self):
        primary_header = self.read_bytes(SECTOR, SECTOR)
        p = decode_header(primary_header, 1, self.sectors)
        if p["alternate_lba"] != self.sectors - 1:
            raise ValueError("Primary GPT backup pointer unexpected")
        p_len = ((p["table_bytes"] + SECTOR - 1) // SECTOR) * SECTOR
        p_raw = self.read_bytes(p["table_lba"] * SECTOR, p_len)
        partitions = decode_entries(p_raw[:p["table_bytes"]], p)
        backup_header = self.read_bytes((self.sectors - 1) * SECTOR, SECTOR)
        b = decode_header(backup_header, self.sectors - 1, self.sectors)
        if b["alternate_lba"] != 1:
            raise ValueError("Backup GPT primary pointer unexpected")
        b_len = ((b["table_bytes"] + SECTOR - 1) // SECTOR) * SECTOR
        b_raw = self.read_bytes(b["table_lba"] * SECTOR, b_len)
        backup_partitions = decode_entries(b_raw[:b["table_bytes"]], b)
        if partitions != backup_partitions or p["disk_guid"] != b["disk_guid"]:
            raise ValueError("Primary and backup GPT disagree")
        if (p["first_usable"], p["last_usable"]) != (b["first_usable"], b["last_usable"]):
            raise ValueError("Primary and backup usable ranges disagree")
        # Both tables have passed CRC before any image target is selected.
        records = (("gpt-primary-header.bin", primary_header),
                   ("gpt-primary-entries.bin", p_raw),
                   ("gpt-backup-header.bin", backup_header),
                   ("gpt-backup-entries.bin", b_raw))
        for name, contents in records:
            self.commit_bytes(self.backups / name, contents)
        result = dict(sector_size=SECTOR, sectors=self.sectors,
                      size_bytes=self.sectors * SECTOR, primary=p, backup=b,
                      partitions=partitions)
        self.write_json(self.evidence / "gpt.json", result)
        return result

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")

    @staticmethod
    def commit_bytes(path, data):
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("Existing backup differs; refusing overwrite: " + str(path))
            return
        # x mode prevents an accidental overwrite, including a race.
        with path.open("xb") as out:
            out.write(data)
        path.chmod(0o400)

    def inventory(self):
        identity = self.guard()
        self.write_json(self.evidence / "identity.json", identity)
        commands = {
            "getprop.txt": "getprop",
            "device-readonly.txt": "id; uname -a; getenforce; cat /proc/cmdline; cat /proc/partitions; "
                "ls -l /dev/block/by-name; ls -l /dev/block/mmcblk0*; cat /proc/mounts; "
                "for p in /sys/class/block/mmcblk0*; do echo \"$p\"; cat \"$p/start\" \"$p/size\" 2>/dev/null; done",
        }
        for filename, command in commands.items():
            (self.evidence / filename).write_text(self.shell(command))
        with (self.evidence / "bootctl-readonly.txt").open("w") as out:
            for args in ("get-number-slots", "get-current-slot", "is-slot-bootable 0",
                         "is-slot-marked-successful 0", "is-slot-bootable 1",
                         "is-slot-marked-successful 1"):
                p = subprocess.run(self.base + ["shell", "bootctl " + args],
                                   capture_output=True, timeout=20)
                out.write("$ bootctl {}\nexit={}\n".format(args, p.returncode))
                out.write(p.stdout.decode("utf-8", "replace"))
                out.write(p.stderr.decode("utf-8", "replace"))
        gpt = self.gpt()
        print("Verified primary + backup GPT: {} partitions, {} bytes".format(
            len(gpt["partitions"]), gpt["size_bytes"]), flush=True)

    def verify_sysfs(self, entry):
        node = "mmcblk0p{}".format(entry["index"])
        base = "/sys/class/block/" + node
        values = self.shell("cat {}/start {}/size; readlink /dev/block/by-name/{}".format(
            base, base, entry["name"])).splitlines()
        if len(values) != 3 or values != [str(entry["start_sector"]), str(entry["sectors"]),
                                         "/dev/block/" + node]:
            raise ValueError("GPT/sysfs/by-name disagreement for " + entry["name"])

    def backup_range(self, name, offset, length, double_read=False):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError("Invalid image name")
        target = self.backups / (name + ".img")
        dd = self.dd(offset, length)
        copies = 2 if double_read else 1
        first_hash = None
        last_tmp = None
        try:
            for number in range(copies):
                fd, tmp = tempfile.mkstemp(prefix=name + ".", suffix=".partial", dir=str(self.backups))
                last_tmp = Path(tmp)
                with os.fdopen(fd, "wb") as stream:
                    subprocess.run(self.base + ["exec-out", dd], stdout=stream,
                                   stderr=subprocess.PIPE, timeout=900, check=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                if last_tmp.stat().st_size != length:
                    raise ValueError("Raw image length mismatch for " + name)
                current_hash = digest_file(last_tmp)
                if first_hash is not None and current_hash != first_hash:
                    raise ValueError("Independent reads disagree for " + name)
                first_hash = current_hash
                if number + 1 < copies:
                    last_tmp.unlink()
                    last_tmp = None
                print("{}: read {}/{}, {} bytes, {}".format(name, number + 1, copies,
                      length, current_hash), flush=True)
            remote = self.shell(dd + " | sha256sum", timeout=900).strip()
            if not re.fullmatch(r"[0-9a-f]{64}\s+[-]", remote) or remote.split()[0] != first_hash:
                raise ValueError("Device/Mac SHA256 mismatch for " + name)
            if target.exists():
                if target.stat().st_size != length or digest_file(target) != first_hash:
                    raise ValueError("Existing image differs; refusing overwrite: " + str(target))
            else:
                os.link(str(last_tmp), str(target))
                target.chmod(0o400)
            with target.open("rb") as stream:
                first16 = stream.read(16)
                stream.seek(0)
                all_zero = all(chunk.count(0) == len(chunk)
                               for chunk in iter(lambda: stream.read(1024 * 1024), b""))
            return dict(name=name, filename=target.name, offset_bytes=offset,
                        size_bytes=length, sha256=first_hash, independent_full_reads=copies,
                        device_sha256_verified=True, all_zero=all_zero,
                        first16_hex=first16.hex())
        finally:
            if last_tmp is not None and last_tmp.exists():
                last_tmp.unlink()

    def backup(self):
        identity = self.guard()
        gpt = self.gpt()
        by_name = {entry["name"]: entry for entry in gpt["partitions"]}
        requested = list(dict.fromkeys(self.args.names))
        if set(requested) - set(by_name):
            raise ValueError("Unknown partition(s): " + ", ".join(sorted(set(requested) - set(by_name))))
        # Verify every target before the first partition download begins.
        for name in requested:
            self.verify_sysfs(by_name[name])
        manifest_path = self.backups / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        if manifest and manifest.get("identity") != identity:
            raise ValueError("Existing manifest belongs to a different device")
        manifest.update(identity=identity, adb_serial=self.args.serial, raw_device=RAW_DEVICE,
                        emmc_size_bytes=gpt["size_bytes"], sector_size=SECTOR,
                        gpt_primary_and_backup_crc_verified=True)
        records = {r["name"]: r for r in manifest.get("images", [])}
        for name in requested:
            e = by_name[name]
            r = self.backup_range(name, e["start_sector"] * SECTOR, e["size_bytes"],
                                  double_read=name in ("boot_a", "boot_b"))
            r.update(partition_index=e["index"], start_sector=e["start_sector"])
            records[name] = r
            manifest["images"] = list(records.values())
            manifest["updated_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self.write_json(manifest_path, manifest)
        if self.args.include_boot_gap:
            first_start = min(e["start_sector"] for e in gpt["partitions"])
            if first_start > 128 * 1024 * 1024 // SECTOR:
                raise ValueError("Unexpected boot gap larger than 128 MiB")
            records["emmc-prepartition"] = self.backup_range("emmc-prepartition", 0, first_start * SECTOR)
            manifest["images"] = list(records.values())
            self.write_json(manifest_path, manifest)
        lines = []
        for image in sorted(self.backups.glob("*.img")):
            lines.append("{}  {}".format(digest_file(image), image.name))
        for image in sorted(self.backups.glob("gpt-*.bin")):
            lines.append("{}  {}".format(digest_file(image), image.name))
        (self.backups / "SHA256SUMS").write_text("\n".join(lines) + "\n")
        print("All requested images verified. No device writes performed.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default="adb", help="Path to installed Android Debug Bridge")
    parser.add_argument("--serial")
    parser.add_argument("--device-config")
    parser.add_argument("--output", default=str(ROOT))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory", help="Save read-only identity, properties and GPT evidence")
    backup = sub.add_parser("backup", help="Read and independently verify selected GPT partitions")
    backup.add_argument("--names", nargs="+", default=["boot_a", "boot_b"])
    backup.add_argument("--include-boot-gap", action="store_true",
                        help="Also save LBA 0 through the first GPT partition start (Allwinner boot area)")
    args = parser.parse_args()
    reader = Reader(args)
    if args.command == "inventory":
        reader.inventory()
    else:
        reader.backup()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print("STOP: {}".format(exc), file=sys.stderr)
        sys.exit(1)
