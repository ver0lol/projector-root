#!/usr/bin/env python3
"""Claim only the single unpublished handoff commit. No network or force-push."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

BOOTSTRAP_EMAIL = 'handoff@example.invalid'
BOOTSTRAP_MESSAGE = 'Prepare source snapshot for the projector owner'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True, help='Your display name in Git commits')
    parser.add_argument('--email', required=True, help='Your verified GitHub email or exact GitHub noreply address')
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--git', default=shutil.which('git') or 'git')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if not args.name.strip() or any(ord(c) < 32 or c in '<>' for c in args.name):
        raise ValueError('Provide a normal Git display name')
    if not re.fullmatch(r'[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+', args.email) or args.email.endswith('.invalid'):
        raise ValueError('Use your own verified email or your exact GitHub noreply address')
    root = args.repo.resolve()
    if not (root/'.git').is_dir() or not (root/'HANDOFF.json').is_file():
        raise ValueError('Not a complete handoff repository; open the extracted project folder')
    env = os.environ.copy()
    for key in ['GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL', 'GIT_AUTHOR_DATE', 'GIT_COMMITTER_DATE']:
        env.pop(key, None)
    command = [args.git, '-C', str(root)]
    def git(*parts):
        return subprocess.check_output(command+list(parts), text=True, env=env).strip()
    if git('status', '--porcelain'):
        raise ValueError('Working tree has changes. Preserve them; claim ownership before editing files')
    if git('remote'):
        raise ValueError('A remote is already configured; this helper will not rewrite potentially published history')
    if git('tag') or git('rev-list', '--count', '--all') != '1':
        raise ValueError('Only the untouched single-commit handoff is supported; no existing history will be rewritten')
    if git('log', '-1', '--format=%B') != BOOTSTRAP_MESSAGE:
        raise ValueError('This is not the prepared bootstrap commit')
    author = git('log', '-1', '--format=%an%n%ae').splitlines()
    if author == [args.name, args.email]:
        print('Already configured for this owner; no history changes needed')
        return
    if author[-1] != BOOTSTRAP_EMAIL:
        raise ValueError('The commit already belongs to another identity; refusing to replace it')
    tree = git('rev-parse', 'HEAD^{tree}')
    if args.dry_run:
        print('Ready: only the unpublished bootstrap author/committer and local Git identity would change')
        return
    git('config', '--local', 'user.name', args.name)
    git('config', '--local', 'user.email', args.email)
    with tempfile.TemporaryDirectory(prefix='handoff-empty-hooks-') as hooks:
        subprocess.run(command+['-c', 'core.hooksPath='+hooks, '-c', 'commit.gpgsign=false',
                               'commit', '--amend', '--reset-author', '--no-edit'], check=True, env=env)
    if git('rev-parse', 'HEAD^{tree}') != tree:
        raise RuntimeError('Unexpected source tree change')
    identity = git('log', '-1', '--format=%an%n%ae%n%cn%n%ce').splitlines()
    if identity != [args.name, args.email, args.name, args.email]:
        raise RuntimeError('Git did not apply the requested author and committer')
    print('Owner configured. Source files and third-party notices are unchanged. Nothing was uploaded.')


if __name__ == '__main__':
    main()
