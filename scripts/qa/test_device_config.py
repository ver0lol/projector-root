"""Identity guards are checked entirely offline; no device discovery."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_config import load_device, PLATFORM


class DeviceConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'device.json'
        self.valid = {'adb_serial': '127.0.0.1:5555', 'expected': dict(PLATFORM, **{'ro.serialno': 'test-projector-serial'})}

    def load(self, config):
        self.path.write_text(json.dumps(config))
        return load_device(self.path)

    def test_independently_pinned_identity_is_preserved(self):
        self.assertEqual(self.load(self.valid), self.valid)

    def test_missing_and_template_configuration_fail_closed(self):
        with self.assertRaises(ValueError): load_device(self.path)
        example = Path(__file__).resolve().parents[2]/'config/device.example.json'
        with self.assertRaises(ValueError): load_device(example)

    def test_wrong_or_incomplete_platform_is_rejected(self):
        for prop in PLATFORM:
            config = copy.deepcopy(self.valid); config['expected'][prop] = 'different'
            with self.subTest(prop=prop), self.assertRaises(ValueError): self.load(config)

    def test_property_names_cannot_be_shell_instructions(self):
        config = copy.deepcopy(self.valid); config['expected']['ro.serialno; arbitrary-command'] = 'value'
        with self.assertRaises(ValueError): self.load(config)

    def test_control_characters_and_non_objects_are_rejected(self):
        for field in ['adb_serial', 'ro.serialno']:
            config = copy.deepcopy(self.valid)
            if field == 'adb_serial': config[field] = 'target\ncommand'
            else: config['expected'][field] = 'serial\ncommand'
            with self.subTest(field=field), self.assertRaises(ValueError): self.load(config)
        with self.assertRaises(ValueError): self.load([])


if __name__ == '__main__':
    unittest.main()
