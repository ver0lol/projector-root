#!/usr/bin/env python3
"""Offline source checks. No ADB, device access, credentials or downloads."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = {'backups', 'artifacts', 'evidence', 'tools', 'work', 'dist', 'local', 'node_modules'}
FORBIDDEN = {'.apk', '.apks', '.aab', '.img', '.bin', '.dex', '.class', '.jks', '.keystore', '.pem', '.key', '.p12', '.pfx'}


def main():
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
    failures = []
    files = sorted({name for name in names if name and (ROOT/name).is_file()})
    for name in files:
        path = ROOT/name
        if Path(name).parts[0] in PRIVATE or name == 'project.json' or path.suffix in FORBIDDEN:
            failures.append(name+': private/generated file must not be tracked'); continue
        if path.is_symlink():
            failures.append(name+': symlink is not an auditable source file'); continue
        if path.stat().st_size > 5*1024*1024:
            failures.append(name+': unexpected large artifact'); continue
        if name == 'vendor/avbtool.py':
            if hashlib.sha256(path.read_bytes()).hexdigest() != 'e5a664a38db623da00f080219bc0ee60a640a9dc4a872803616fae4938ac749b':
                failures.append('Reviewed AOSP avbtool was modified')
            continue
        if name.startswith('docs/images/') and path.suffix == '.png':
            if not path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'):
                failures.append(name+': invalid screenshot PNG')
            continue  # Reviewed documentation screenshots, never runtime/PIN captures.
        if name.endswith('.tar.gz'):
            continue  # Pinned upstream source archives are documented in README.md.
        try: text = path.read_text()
        except UnicodeDecodeError:
            failures.append(name+': unexpected binary file'); continue
        patterns = [r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----', r'\bgh[pousr]_[A-Za-z0-9]{25,}', r'\bgithub_pat_[A-Za-z0-9_]{30,}', r'https://vpn\.(?!example\.)', r'/' + r'Users/[^/\s]+/']
        if any(re.search(pattern, text) for pattern in patterns):
            failures.append(name+': possible private material (value intentionally not logged)')
        try:
            if path.suffix == '.py': ast.parse(text, filename=name)
            if path.suffix == '.json': json.loads(text)
            if path.suffix == '.xml': ET.fromstring(text)
        except (SyntaxError, ValueError, ET.ParseError) as error:
            failures.append(name+': syntax check failed: '+type(error).__name__)
        if path.suffix == '.md':
            for target in re.findall(r'\]\(([^)]+)\)', text):
                if re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', target) or target.startswith('#'): continue
                dest = (path.parent/target.split('#')[0]).resolve()
                try: relative = dest.relative_to(ROOT)
                except ValueError: continue  # Historical sibling release artifacts.
                if relative.parts and relative.parts[0] in PRIVATE: continue
                if not dest.exists(): failures.append(name+': missing local link '+target)
    if (ROOT/'singbox-integration/upstream/SHA256SUMS').is_file():
        archive_root = ROOT/'singbox-integration/upstream'
        for line in (archive_root/'SHA256SUMS').read_text().splitlines():
            digest, name = line.split()
            if Path(name).name != name or hashlib.sha256((archive_root/name).read_bytes()).hexdigest() != digest:
                failures.append('Upstream source archive digest mismatch')
    for name in ['local/device.json', 'local/vpn.json', 'work/example.jks', 'dist/example.apk', 'backups/boot_a.img', 'evidence/example.json', 'project.json']:
        result = subprocess.run(['git', 'check-ignore', '--no-index', '-q', name], cwd=ROOT)
        if result.returncode != 0: failures.append('Ignore rule missing for '+name)
    if (ROOT/'launcher/AndroidManifest.xml').is_file():
        manifest = ET.parse(ROOT/'launcher/AndroidManifest.xml').getroot()
        version = manifest.get('{http://schemas.android.com/apk/res/android}versionName')
        if version not in (ROOT/'README.md').read_text(): failures.append('README version does not match manifest version')
    # A missing/template config must fail before any ADB process can start.
    from project_config import load_device
    for name in [ROOT/'config/device.example.json', ROOT/'local/does-not-exist.json']:
        try: load_device(name)
        except ValueError: pass
        else: failures.append('Unconfigured device target was accepted')
    if failures:
        print('\n'.join(failures), file=sys.stderr); return 1
    print('Source checks passed:', len(files), 'files; private paths ignored; device guards fail closed. No device access.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
