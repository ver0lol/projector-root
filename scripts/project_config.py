"""Local device identity, never discovered or accepted from a connected device."""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = {
    "oem.product.model": "P30_H713_V1.1_CHIHI_emcp",
    "ro.hardware": "sun50iw12p1",
    "ro.board.platform": "ares",
    "ro.product.cpu.abi": "armeabi-v7a",
}


def load_device(path=None):
    source = Path(path or os.environ.get("RICE_DEVICE_CONFIG", ROOT / "local/device.json"))
    if not source.is_file():
        raise ValueError("Configure local/device.json from config/device.example.json first")
    config = json.loads(source.read_text())
    if not isinstance(config, dict):
        raise ValueError("Device configuration must be an object")
    expected = config.get("expected", {})
    if not isinstance(expected, dict) or set(expected) != set(PLATFORM) | {"ro.serialno"}:
        raise ValueError("Expected identity must contain exactly the supported property names")
    serial = expected.get("ro.serialno", "")
    if not isinstance(serial, str) or not re.fullmatch(r"[A-Za-z0-9_-]{4,128}", serial) or serial.startswith("REPLACE_"):
        raise ValueError("Pin the independently verified ro.serialno in local/device.json")
    if any(expected.get(key) != value for key, value in PLATFORM.items()):
        raise ValueError("This project supports only its pinned P30 / H713 / ARM32 platform")
    target = config.get("adb_serial")
    if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_.:\[\]-]{1,255}", target) or target.startswith("192.0.2."):
        raise ValueError("Set the actual ADB target in local/device.json")
    return config
