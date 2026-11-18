# AtlasA1 U-Boot sources for the eMMC media boot chain

The deployed media boot chain uses the CPU-D board sources against U-Boot v2026.10 commit `5508406582f6f7120dce0c4b3059819a41788db2`. The patch and complete saved configuration reconstruct its 21 board-specific source files, including the eMMC and processor clock code. [Source hashes](source-hashes.json) · [Saved configuration](uboot.config)

```sh
git clone --depth 1 --branch v2026.10 https://github.com/u-boot/u-boot.git /tmp/atlas-upstream-uboot
python3 tools/prepare_atlas_release_sources.py u-boot \
  --upstream /tmp/atlas-upstream-uboot --output /tmp/atlas-uboot
mkdir /tmp/atlas-uout
cp /tmp/atlas-uboot/.config.atlasa1 /tmp/atlas-uout/.config
export ARCH=arm CROSS_COMPILE=aarch64-linux-gnu-
make -C /tmp/atlas-uboot O=/tmp/atlas-uout olddefconfig
make -C /tmp/atlas-uboot O=/tmp/atlas-uout -j"$(nproc)"
```

The original configuration contains the media boot paths and root partition ID. U-Boot embeds its build timestamp. The Ubuntu release packer builds a dedicated boot command, pairs U-Boot with the fixed LPDDR4x SPL, stage64 and TF-A, and computes the package CRCs. [Release boot packer](../../../tools/build_atlasa1_release_boot.py)
