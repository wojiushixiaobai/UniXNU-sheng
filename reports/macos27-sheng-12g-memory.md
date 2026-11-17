# macOS 27.3 on the 12 GiB sheng: firmware memory map and ADT windows

Recorded 2026-11-18 on Xiaomi Pad 6S Pro 12.4 (`sheng`, SM8550, 12 GiB / 256 GB,
SN 82c1c06f). OS: macOS 27.3 build 26C5035r. The userdata root image is the
`root_storage=true` lab artifact of run 44087029271 (f0de5d8).

## Question

The certified CrossDarwin 218FDCCE recovery template boots the payload but
exposes only the low DRAM bank, so the macOS ADT window stays at 2 GiB
(`Memory 2GB` in About, heavy swap pressure). What does the 12 GiB board need
to give XNU a larger managed span?

## DRAM layout of the 12 GiB unit

| Range | Size | State |
| --- | --- | --- |
| `0x80000000..0x100000000` | 2 GiB | low bank; firmware/secure carveouts leave 0.862 GiB free |
| `0x880000000..0x8C0000000` | 1 GiB | bank 1 entry; `0x8BB300000..0x8C0000000` (77 MiB) is inaccessible (external abort) |
| `0x8C0000000..0xB00000000` | 9 GiB | bank 1 high part |

Usable DRAM by the firmware map: 0.862 + 0.947 + 9 = 10.787 GiB. The other
1.213 GiB is secure-world/DSP/display/UEFI carveouts, the 77 MiB hole and the
stock `Kernel` reservation.

## Firmware

The certified template has no `ShengBootMemoryDxe` expansion, so its map holds
no bank 1 (largest free run is the `0xBAB00000 + 0xD1C00000` DXE heap). A
self-built uefi-sheng firmware from `8156d2e54a9f57818dc8b415a1a7dc28ad9ba4be`
was used instead, with `firmware/patches/uefi-sheng-dram-bank1-12g.patch`
applied locally: it raises `DRAM bank 1 high` from 5 GiB (8 GiB layout) to
9 GiB (12 GiB layout).

Build (local, WSL Debian; the stock-firmware porting inputs were already
cached by the preflight):

```sh
cd /opt/unixnu-work/UniXNU
python3 ./unixnu build firmware -D ../fork --uefi ../uefi-sheng \
  --edk2 out/feeds/mu_basecore-8f10084c --skip-preflight -j 8
# -> ../uefi-sheng/Build/shengPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd
```

The firmware FV (3 MiB, sha256 `bc206ce1...`) replaces the certified template
FV inside the boot image; BootShim, the firmware DTB collection and the payload
are unchanged. The payload keeps the CI kernel and Data seed.

## Boot images tested

The payload ADT `darwinboot-memory-size` was patched to each window size and
the payload's unixnu.toml entries gained `uefi-watchdog=1` (the self-built
firmware arms the Gunyah watchdog at ExitBootServices).

| Window | Image sha256 | Result |
| --- | --- | --- |
| 2 GiB (template) | `fd82b2a3...` | boot menu and desktop; Memory 2 GB |
| 4 GiB | `75a9b607...` | desktop; smooth |
| 6 GiB | `f4885a45...` | desktop; smooth (6 GB) |
| 8 GiB | `b222d4cf...` | desktop; first boot after a flash can fail and the second Boot succeeds |
| 9 GiB at `0x8C0000000` | `a932082d...` | aborts before any console output; device returns to fastboot after about 80 s |
| 9.9 GiB spanning the hole (`0x880000000 + 0x27B000000`) | `c4a20fea...` | screen flashes with no text, then the UniXNU menu returns |

The 8 GiB window requires
`patches/xnu-12377/zzz-macos-bootstrap-capacity-18g.patch` (device patch 156 of
the 27 series): `BOOTSTRAP_TABLE_SIZE` grows from 16 to 40 pages because native
4 KiB pages need one L2 page per GiB of span for the physical and for the
virtual mapping, while the 16-page pool only covers a 6 GiB span. The rebuilt
kernel is 143,835,136 bytes sha256 `14531eac...`; the kernel patch and payload
are the only changes against the previous artifact.

## Open questions

1. Why does the 9 GiB window fail although bank 1 high spans 9 GiB and the
   firmware allocates from the low bank? The 8 GiB window needs an 8 GiB
   contiguous free run, and `DarwinWindowStart` ranks starts by usable memory,
   so a single firmware allocation inside the 9 GiB run removes the 9 GiB
   candidate; this needs the DarwinBoot diagnostics or window clipping.
2. The 9.9 GiB hole-spanning window should only need the head (about 530 MiB)
   in the 947 MiB bank-1 low run; it aborts before the kernel console, so the
   failure is in DarwinBoot or in the firmware's handling of the window span,
   not in the kernel bootstrap pool (24 pages are needed, 40 are available).
3. The first Boot after flashing can fail once at 8 GiB and succeed on retry;
   record whether this depends on the UEFI allocation order per boot.
4. Swap and compressor state on the 8 GiB desktop are unverified: the macOS
   report expects dynamic swap on the Data volume
   (`/private/var/vm/swapfile*`).

## Next steps

- Add DarwinBoot diagnostics that keep the failure (window start, usable bytes,
  free runs) on screen instead of returning to the menu, or implement window
  clipping to the largest free run.
- Reclaim firmware reservations (the stock 256 MiB `Kernel` window, the 37 MiB
  `Display Demura` gap) to add usable runs below the window.
- Keep the 8 GiB image as the current working configuration; 9 to 9.9 GiB are
  the remaining targets of `ROADMAP.md` Phase 1.