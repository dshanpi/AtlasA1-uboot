#!/usr/bin/env python3
"""Build AtlasA1 v4 SPL, stage64, BL31 and CPU-D U-Boot from a clean directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zlib


ROOT = Path(__file__).resolve().parents[1]
TFA_COMMIT = '7d322909068fc4bb4ee35e9e43c78ed68d98619b'
TFA_ARCHIVE_SHA = 'fd1e430b4e6998a802577bf7d58f4b5721cb97ed03b408901767c244474c884a'
BASE_LBA = 24576
SPL_SHA = 'a1d821eaee602fdf8b481c38cf4790d0f8a6f31539dd9fc6c1a714c1e92bd0ea'
STAGE_SHA = '44aeb8e56f64f2ccd80a1cd286460eda6d0232f9cc1e82618866826b23298535'
BL31_SHA = '81f53c1d1f571c74bd907317d813a5ed56c64382479caa75edd3cec43ada6dc6'
ROOT_ID = 'a0085546-4166-744a-a353-fca9272b8e48'


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def require_hash(path, expected):
    if digest(path) != expected:
        raise ValueError(f'incorrect SHA256 for {path}')


def build_baseline(args, stage):
    for tree in ('spl', 'boards', 'config', 'patches/u-boot', 'patches/tf-a'):
        shutil.copytree(ROOT/tree, stage/tree)
    for name in ('tools/build_t736_emmc_spl.py', 'tools/build_t736_source_handoff.py',
                 'tools/build_source_uboot_cpu_d.sh', 'evidence/source-uboot-backup-audit-20261115.json'):
        (stage/name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/name, stage/name)
    archives = stage/'sources/archives'
    archives.mkdir(parents=True)
    (archives/'u-boot-upstream.tar.gz').symlink_to(args.uboot_archive.resolve(strict=True))
    subprocess.run([sys.executable, 'tools/build_t736_emmc_spl.py', '--variant', 'v4',
                    '--ddr-library', str(args.ddr_library.resolve(strict=True))], cwd=stage, check=True)
    subprocess.run([sys.executable, 'tools/build_t736_source_handoff.py'], cwd=stage, check=True)
    require_hash(args.tfa_archive, TFA_ARCHIVE_SHA)
    tfa = stage/'build/tfa'
    tfa.mkdir()
    subprocess.run(['tar', '-xf', args.tfa_archive.resolve(), '--no-same-owner',
                    '--strip-components=1', '-C', tfa], check=True)
    subprocess.run(['patch', '--fuzz=0', '-p1', '-i',
                    stage/'patches/tf-a/0001-t736-bl31-dram-layout.patch'], cwd=tfa, check=True)
    # Preserve the pinned firmware's build identification and reproduce its bytes.
    subprocess.run(['make', '-C', tfa, f'-j{args.jobs}', 'CROSS_COMPILE=aarch64-linux-gnu-',
                    'PLAT=sun60i_a733', 'DEBUG=1', 'LOG_LEVEL=40', 'PRELOADED_BL33_BASE=0x52000000',
                    'BUILD_STRING=t736-independent-candidate-'+TFA_COMMIT[:8],
                    'BUILD_MESSAGE_TIMESTAMP="06:57:46, Nov 13 2026"', 'bl31'], check=True)
    subprocess.run(['sh', 'tools/build_source_uboot_cpu_d.sh'], cwd=stage, check=True,
                   env=dict(os.environ, T736_JOBS=str(args.jobs)))


def build_uboot(stage, work, jobs):
    cpud = stage/'build/source-uboot-cpu-d'
    out = work/'uboot-release'
    out.mkdir()
    config = (cpud/'output/.config').read_text()
    base_args = f'setenv bootargs earlycon=uart8250,mmio32,0x02500000 console=ttyS0,115200 root=PARTUUID={ROOT_ID} rootwait rw clk_ignore_unused'
    fast_args = ('t736_mmc_pio.enable_hs200=1 t736_mmc_pio.hs200_max_hz=200000000 '
                 't736_mmc_pio.enable_hs400=1 t736_mmc_pio.hs400_max_hz=200000000 '
                 't736_cedar_wc.heap_limit_mib=256')
    command = ('setenv kernel_comp_addr_r 0x68000000; if test x${t736_recovery} = x1; then '
               f'{base_args} systemd.unit=rescue.target; else {base_args} {fast_args}; fi; '
               'if mmc dev 0 && ext4load mmc 0:4 0x60000000 /boot/vmlinuz-6.18.63-t736-armbian '
               '&& setenv kernel_comp_size ${filesize} && ext4load mmc 0:4 0x64000000 '
               '/boot/initrd.img-6.18.63-t736-armbian; then setenv rdsize ${filesize}; '
               'if ext4load mmc 0:4 0x63000000 /boot/dtb/allwinner/t736-mainline-bringup.dtb; '
               'then booti 0x60000000 0x64000000:${rdsize} 0x63000000; fi; fi')
    lines = [s for s in config.splitlines() if s.startswith('CONFIG_BOOTCOMMAND=')]
    if len(lines) != 1:
        raise ValueError('expected exactly one CPU-D boot command')
    config = config.replace(lines[0], 'CONFIG_BOOTCOMMAND='+json.dumps(command))
    (out/'.config').write_text(config)
    make = ['make', '-C', cpud/'source', f'O={out}', 'ARCH=arm', 'CROSS_COMPILE=aarch64-linux-gnu-']
    env = dict(os.environ, SOURCE_DATE_EPOCH='1794960000', TZ='UTC')
    subprocess.run(make+['olddefconfig'], env=env, check=True)
    if (out/'.config').read_text() != config:
        raise ValueError('release configuration changed during olddefconfig')
    subprocess.run(make+[f'-j{jobs}'], env=env, check=True)
    binary = out/'u-boot.bin'
    if ('bootcmd='+command).encode()+b'\0' not in binary.read_bytes():
        raise ValueError('compiled U-Boot contains a different boot command')
    return binary


def pack(stage, work, uboot):
    spl = stage/'build/emmc-spl-v4/emmc-spl.bin'
    loader = stage/'build/source-handoff-v1/stage64.bin'
    bl31 = stage/'build/tfa/build/sun60i_a733/debug/bl31.bin'
    for path, sha in ((spl, SPL_SHA), (loader, STAGE_SHA), (bl31, BL31_SHA)):
        require_hash(path, sha)
    package = bytearray(512)
    struct.pack_into('<IIII', package, 0, 0x36333754, 0x314C5053, 1, 3)
    images = []
    for index, (path, address, maximum) in enumerate(((loader, 0x47000000, 8192),
                (bl31, 0x48000000, 0x200000), (uboot, 0x52000000, 0x100000))):
        data = path.read_bytes()
        if not 0 < len(data) <= maximum:
            raise ValueError(f'invalid payload length for {path}')
        lba = BASE_LBA + len(package)//512
        crc = zlib.crc32(data)
        struct.pack_into('<IIII', package, 32 + index*16, lba, address, len(data), crc)
        package.extend(data)
        package.extend(bytes(-len(data) % 512))
        images.append(dict(name=path.name, lba=lba, address=hex(address), size=len(data),
                           sha256=digest(path), crc32=f'{crc:08x}'))
    if BASE_LBA + len(package)//512 >= 32800:
        raise ValueError('payload exceeds the reserved eMMC range')
    struct.pack_into('<I', package, 16, zlib.crc32(package[:512]))
    result = work/'payload'
    result.mkdir()
    for path in (spl, loader, bl31, uboot):
        shutil.copy2(path, result/path.name)
    boot = result/'boot-package.bin'
    boot.write_bytes(package)
    (result/'boot-manifest.json').write_text(json.dumps(dict(
        root_partuuid=ROOT_ID, spl_sha256=SPL_SHA, boot_package_sha256=digest(boot),
        package_start_lba=BASE_LBA, components=images, hardware_tested=False), indent=2)+'\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ('uboot-archive', 'tfa-archive', 'ddr-library', 'work'):
        ap.add_argument('--'+name, type=Path, required=True)
    ap.add_argument('--jobs', type=int, default=4)
    args = ap.parse_args()
    if args.jobs < 1:
        ap.error('--jobs must be positive')
    work = args.work.resolve()
    work.mkdir(parents=True)
    stage = work/'boot-project'
    build_baseline(args, stage)
    pack(stage, work, build_uboot(stage, work, args.jobs))


if __name__ == '__main__':
    main()
