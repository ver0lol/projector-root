#!/usr/bin/env python3
"""Offline image validation. Never accesses the device or changes input images."""
import argparse
import hashlib
import json
import struct
from pathlib import Path


def align(n, page):
    return (n + page - 1) // page * page


def inspect(path):
    data = path.read_bytes()
    result = {"file": str(path), "bytes": len(data),
              "sha256": hashlib.sha256(data).hexdigest(),
              "first16_hex": data[:16].hex(), "all_zero": not any(data),
              "last_nonzero_end": len(data.rstrip(b"\0"))}
    sections = {}
    if data[:8] == b"ANDROID!":
        version = struct.unpack_from("<I", data, 40)[0]
        result.update(format="ANDROID!", header_version=version)
        if version in (3, 4):
            kernel_size, ramdisk_size = struct.unpack_from("<II", data, 8)
            page = 4096
            header_size = struct.unpack_from("<I", data, 20)[0]
            result["cmdline"] = data[44:1580].split(b"\0", 1)[0].decode("utf-8", "replace")
            if header_size not in (1580, 1584):
                raise ValueError(f"Unexpected boot header size {header_size}")
        elif version in (0, 1, 2):
            kernel_size = struct.unpack_from("<I", data, 8)[0]
            ramdisk_size = struct.unpack_from("<I", data, 16)[0]
            page = struct.unpack_from("<I", data, 36)[0]
            if page < 512 or page > 65536 or page & (page - 1):
                raise ValueError("Invalid boot page size")
            header_size = page
        else:
            raise ValueError(f"Unsupported boot header {version}")
        result.update(page_size=page, header_size=header_size,
                      kernel_size=kernel_size, ramdisk_size=ramdisk_size)
        sections = {"kernel": (page, kernel_size),
                    "ramdisk": (page + align(kernel_size, page), ramdisk_size)}
    elif data[:8] == b"VNDRBOOT":
        version, page = struct.unpack_from("<II", data, 8)
        if version not in (3, 4) or page < 512 or page > 65536 or page & (page - 1):
            raise ValueError("Invalid vendor boot header")
        ramdisk_size = struct.unpack_from("<I", data, 24)[0]
        header_size, dtb_size = struct.unpack_from("<II", data, 2096)
        result.update(format="VNDRBOOT", header_version=version, page_size=page,
                      header_size=header_size, ramdisk_size=ramdisk_size,
                      dtb_size=dtb_size,
                      cmdline=data[28:2076].split(b"\0", 1)[0].decode("utf-8", "replace"))
        sections = {"vendor_ramdisk": (align(header_size, page), ramdisk_size),
                    "dtb": (align(header_size, page) + align(ramdisk_size, page), dtb_size)}
    elif data[:4] == b"AVB0":
        result["format"] = "AVB0"
        result["avb_flags"] = struct.unpack_from(">I", data, 120)[0]
        result["avb_algorithm_type"] = struct.unpack_from(">I", data, 28)[0]
    else:
        result["format"] = "zero-filled" if result["all_zero"] else "other"
    result["sections"] = {}
    for name, (offset, size) in sections.items():
        if offset + size > len(data):
            raise ValueError(f"Truncated {name} in {path}")
        result["sections"][name] = {"offset": offset, "size": size,
            "sha256": hashlib.sha256(data[offset:offset+size]).hexdigest()}
    if data[-64:-60] == b"AVBf":
        major, minor, original_size, vbmeta_offset, vbmeta_size = struct.unpack_from(
            ">IIQQQ", data, len(data) - 60)
        result["avb_footer"] = dict(major=major, minor=minor,
            original_image_size=original_size, vbmeta_offset=vbmeta_offset,
            vbmeta_size=vbmeta_size)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = [inspect(p) for p in args.images]
    report = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
    print(report, end="")


if __name__ == "__main__":
    main()
