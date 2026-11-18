#!/usr/bin/env python3
"""Reconstruct the board-tested Linux, U-Boot or PowerVR module sources."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


REVISIONS = {
    'linux': ('1cdda838e8475edf89b73ff0cc952005dd0b1fa7', 'media-v30-replay', 'kernel.config'),
    'u-boot': ('5508406582f6f7120dce0c4b3059819a41788db2', 'media-v28-replay', 'uboot.config'),
}
ARCHIVES = {
    'linux': ('c41bdd52b610c75613effe80c6abf236efce706b3304fe2f023bf912413f9f6e', 1),
    'u-boot': ('af3cca26c2ea2a352efc05980d991b7310959dc431c427ad3250e832a221abd8', 0),
}


def verify(path, expected):
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError(f'Unexpected source contents: {path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component', choices=(*REVISIONS, 'pvr'))
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--upstream', type=Path, help='Local Git repository containing the official revision')
    source.add_argument('--archive', type=Path, help='Previously archived upstream sources, verified by SHA-256')
    parser.add_argument('--output', type=Path, required=True, help='New source directory')
    parser.add_argument('--tina', type=Path, help='Tina SDK source root containing bsp/')
    args = parser.parse_args()
    if args.component in ('linux', 'pvr') and not args.tina:
        parser.error('this component requires --tina')
    patches = Path(__file__).resolve().parents[1] / 'patches'
    if args.component == 'pvr':
        if args.archive or args.upstream:
            parser.error('pvr takes its sources from --tina')
        rev = '24.2'
        config = None
        bundle = patches / 'linux' / 'media-v30-pvr-replay'
    else:
        if not (args.archive or args.upstream):
            parser.error('specify --archive or --upstream')
        rev, recipe, config = REVISIONS[args.component]
        bundle = patches / args.component / recipe
    output = args.output.resolve()
    if output.exists():
        parser.error('--output must name a new directory')
    if args.archive:
        archive_sha, strip_components = ARCHIVES[args.component]
        verify(args.archive, archive_sha)
    inputs = (json.loads((bundle / 'tina-display-inputs.json').read_text())
              if args.component == 'linux' else [])
    recorded = json.loads((bundle / 'source-hashes.json').read_text())
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.atlas-source-', dir=output.parent) as work:
        src = Path(work) / 'src'
        src.mkdir()
        if args.component == 'pvr':
            ddk = args.tina / 'bsp/modules/gpu/img-bxm/linux/rogue_km'
            for item in ddk.iterdir():
                if item.name.startswith('binary_'):
                    continue
                dest = src / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, symlinks=True)
                else:
                    shutil.copy2(item, dest, follow_symlinks=False)
        elif args.archive:
            subprocess.run(['tar', '-xf', str(args.archive), f'--strip-components={strip_components}',
                            '-C', str(src)], check=True)
        else:
            with subprocess.Popen(['git', '-C', str(args.upstream), 'archive', '--format=tar', rev],
                                  stdout=subprocess.PIPE) as archive:
                try:
                    subprocess.run(['tar', '-xf', '-', '-C', str(src)], stdin=archive.stdout, check=True)
                finally:
                    archive.stdout.close()
                if archive.wait():
                    raise RuntimeError('git archive did not complete')
        for rel, expected in recorded['upstream_files'].items():
            verify(src / rel, expected)
        for item in inputs:
            dest = src / item['destination']
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.tina / item['source'], dest)
            verify(dest, item['sha256'])
        subprocess.run(['patch', '--batch', '--forward', '--fuzz=0', '-p1', '-i',
                        str(bundle / 'board.patch')], cwd=src, check=True)
        for rel, expected in recorded['board_tree'].items():
            verify(src / rel, expected)
        if config:
            shutil.copyfile(bundle / config, src / '.config.atlasa1')
        src.rename(output)
    print(f'Prepared {args.component} {rev} with {len(recorded["board_tree"])} verified board files at {output}')


if __name__ == '__main__':
    main()
