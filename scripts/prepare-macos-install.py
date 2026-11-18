#!/usr/bin/env python3
"""Prepare a fresh macOS Data seed and signed root windows for sheng packaging."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

DEVICE = Path(__file__).resolve().parents[1]
ROOT = (DEVICE / tomllib.loads((DEVICE / 'board.toml').read_text())['package']['unixnu']).resolve()
sys.path.insert(0, str(ROOT / 'scripts'))
from unixnu.cli import parser as cli_parser, prepare
from unixnu.config import Context
from unixnu.ipsw import Ipsw
from unixnu.userdata import disk_blocks, image_size


def allow_empty_account_password():
    """Let the core account reader accept an empty password; the seed tool hashes whatever string it is given."""
    module = ROOT / 'scripts' / 'macos_account.py'
    text = module.read_text()
    relaxed = "    if not separator or '\\0' in password:"
    guarded = "    if not separator or not password or '\\0' in password:"
    if relaxed in text:
        return
    if guarded not in text:
        raise SystemExit(f'{module}: cannot find the account password check to relax')
    module.write_text(text.replace(guarded, relaxed))
    print('macOS administrator: empty password allowed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reuse-policies', action='store_true', help='use the selected profile\'s signed root headers and public key')
    args = parser.parse_args()
    subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] /
                                      'scripts/extract-files.py')], check=True)
    subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] /
                                      'drivers/Ath12kWifi/tools/fetch-sources.py')], check=True)
    ctx = Context(cli_parser().parse_args(['build', 'kernel', '-D', str(Path(__file__).resolve().parents[1]),
                                      '--os', 'macos-27']))
    prepare(ctx, ['kernel'])
    source = Ipsw(ctx)
    runtime = ctx.work / 'runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    seed = Path(ctx.args.data_ramdisk).expanduser().resolve() if ctx.args.data_ramdisk else runtime / 'data.dbr'
    if ctx.args.data_ramdisk and not seed.is_file():
        raise SystemExit(f'Preserved Data seed not found: {seed}')
    if not seed.exists():
        allow_empty_account_password()
        # No build credential: the administrator has an empty password, so a fresh Data store logs in without one
        # (auto-login stores that empty password in kcpassword). Set a real password in System Settings after boot.
        subprocess.run(['sudo', sys.executable, str(ROOT / 'scripts/make-macos-data-seed.py'),
                        str(source.disk_image('OS')), str(seed), '--gib',
                        str(ctx.board['payload']['runtime']['data_gib']), '--skip-setup',
                        '--user-stdin', '--auto-login'],
                       input=json.dumps({'name': 'unixnu', 'password': ''}).encode(), check=True)
        subprocess.run(['sudo', 'chown', str(os.getuid()), str(seed)], check=True)
    policies = ctx.device / 'scripts/policies' / ctx.work.parent.name
    if args.reuse_policies:
        if any(not (policies / window['name'] / name).is_file()
               for window in ctx.board['userdata']['windows']
               for name in ('manifest.header.bin', 'trusted-key.bin', 'verification.json')):
            raise SystemExit('Import the selected boot image policies with scripts/import-root-policies.py before reusing them')
        print('Reusing the selected profile signed root policy inputs.')
        return
    for window in ctx.board['userdata']['windows']:
        name = window['name']
        image = source.disk_image(window['component'])
        raw = runtime / f'{name}.raw'
        size = image_size(image)
        try:
            with raw.open('wb') as output:
                for block in disk_blocks(image, size):
                    output.write(block)
            volume = ctx.board['root'][name.lower() + '_volume_uuid']
            subprocess.run([sys.executable, str(ROOT / 'scripts/make-root-policies.py'),
                            '--tree', str(ctx.tree), '--output', str(policies),
                            '--window', f'{name}={raw}:{volume}'], check=True)
        finally:
            raw.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
