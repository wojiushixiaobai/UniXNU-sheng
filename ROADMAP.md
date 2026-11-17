# macOS on sheng: fork roadmap

Fork-specific plan for `wojiushixiaobai/UniXNU-sheng`. The upstream repository
stays device configuration only; phases below that need upstream changes are
marked. Target: macOS 27.3 (26C5035r) on the 12 GiB sheng, with a self-built
uefi-sheng firmware and an ADT window sized to the board's DRAM banks.

Status on 2026-11-18: boot transport qualified, self-built firmware and the
rebuilt kernel in place, desktop reached on a userdata root image flashed from
the lab artifact. 4, 6 and 8 GiB ADT windows run; the 8 GiB window needs the
40-page bootstrap pool (device patch 156, CI run 44189136929, kernel
`14531eac`). A 9 GiB window and a 9.9 GiB hole-spanning window abort before
any kernel console output. Current working configuration: 8 GiB window on
firmware FV `bc206ce1`. Record: `reports/macos27-sheng-12g-memory.md`.

Non-goals:

- No Apple boot chain reproduction. iBoot, TSS personalization and the Startup
  Manager cannot be faked; "official" means Apple-shaped configuration and
  boot policy on our own UEFI/DarwinBoot chain.
- No EL2 hypervisor (Snapintosh-style). This port keeps the native kernel.
- No guarantee for Apple services (iCloud, iMessage) even with valid identity;
  Apple Silicon attestation is outside our control and the terms do not allow it.

## Phase 0: boot transport and memory floor (done 2026-11-18)

- Certified CrossDarwin recovery template boots the payload but exposes only the
  low 2 GiB bank.
- Self-built uefi-sheng firmware adds DRAM bank 1 (`0x880000000`), first with
  5 GiB, then 9 GiB for the 12 GiB board.
- 4 GiB and then 6 GiB ADT windows reach the desktop.
- The kernel `BOOTSTRAP_TABLE_SIZE` sized for 6 GiB spans blocked every larger
  window until the 40-page bootstrap pool patch (device patch 156) was added.

## Phase 1: full usable memory

Goal: raise the managed window to the largest single bank-1 span and reclaim the
memory outside it, so macOS reports about 9.5 to 10.5 GiB instead of 8 GiB.

Done:

1. Kernel: `BOOTSTRAP_TABLE_SIZE` 16 to 40 pages
   (`patches/xnu-12377/zzz-macos-bootstrap-capacity-18g.patch`, device patch
   156, commit 111cade). CI run 44189136929 rebuilt the kernel
   (`14531eac`) and the 8 GiB window reaches the desktop.
2. Firmware: the 9 GiB bank-1 entry lives in the local uefi-sheng checkout and
   is captured as `firmware/patches/uefi-sheng-dram-bank1-12g.patch`.
3. Window: an 8 GiB ADT window (`0x200000000`) boots. The first Boot after a
   flash can fail once and succeed on retry; record whether that follows the
   UEFI allocation order.

Remaining:

4. Diagnostics: a 9 GiB window returns to fastboot and a 9.9 GiB
   hole-spanning window shows a flash with no text, so DarwinBoot must keep its
   failure state on screen (window start, usable bytes, free runs) or clip the
   request to the largest free run before another hardware attempt.
5. Firmware: evaluate reclaiming the 256 MiB `Kernel` reservation (stock
   Android window) and the `Display Demura` gap, then re-run the memory-map
   check; that adds usable runs below the window.
6. Adoption: tune `owned_memory_outside_ranges` and the runtime probe so the
   low bank (about 0.86 GiB free) and bank-1 low (947 MiB) are adopted.
7. Verify swap/compressor on the 8 GiB desktop: dynamic swap on the Data
   volume (`/private/var/vm/swapfile*`), `sysctl vm.swapusage`, and a short
   memory-pressure run.

Acceptance: 8 GiB reached and verified on hardware; the next step (9 to 9.9 GiB)
needs the diagnostics above; 30 minutes of memory pressure stays responsive;
`sysctl hw.memsize` matches the window plus adopted pages.

Risks: the 77 MiB hole and firmware carveouts cap the total below 12 GiB; a
window spanning the hole needs the excluded-range path; upstream may reject the
larger bootstrap table.

## Phase 2: identity, persistent NVRAM and boot entries

Goal: replace the placeholder identity that reports "Chip Unknown" and
"SN Unknown" with a persistent, OpenCore PlatformInfo-like identity and boot
policy.

Work items:

1. ADT identity: add `serial-number`, `MLB` (board serial), `ROM` and
   `hardware-uuid` to the ADT product node; keep the Mac17,11 model and
   T6050 chip identity. Source values from a fork-local identity file, never
   from a shared default.
2. Persistent NVRAM: turn the current volatile CHRP proxy
   (`scripts/make-sheng-adt.py`) into a real store. Use the device
   `uefivarstore` partition (0x80000) or a dedicated persisted image; keep
   `boot-args` and `efi-boot-device` across reboots.
3. Boot entries: persist the default boot volume (bless equivalent) via
   `efi-boot-device` and `boot-uuid`; keep the UniXNU menu as the picker and
   make its default follow the persisted entry.
4. Configuration: collect model, serial, MLB, ROM, boot args and quirks into
   one fork-local config consumed by the ADT generator and the payload, so a
   device identity is reproducible from a single file.
5. Verification: `system_profiler SPHardwareDataType` reports model and serial;
   NVRAM and the default boot choice survive a cold boot.

Acceptance: About and `system_profiler` show the configured identity; NVRAM
persists; the boot picker defaults to the persisted volume.

Risks: Apple service attestation remains a hard limit; every device needs its
own serial/MLB pair.

## Phase 3: graphical Recovery

Goal: boot a BaseSystem recovery environment with display and input, as the
prerequisite for any installer flow.

Work items:

1. Recovery profile: add a recovery OS profile that boots the BaseSystem root
   image (about 3.7 GiB, from the paired IPSW) plus the ISC role volume.
2. Display and input in recovery: GOP or native display path, touch and USB
   HID, text console as the fallback.
3. Networking: USB Ethernet first (`usb=1` exists), then WCN7850 Wi-Fi inside
   the recovery kernel.
4. Installer UI: language chooser and `recoveryosd`/`WindowServer`; the blank
   language list is the current blocker in the CrossDarwin work.

Acceptance: recovery boots to an interactive UI with working input; a shell or
installer can read the UFS APFS layout.

Risks: recovery userland is a different service set; graphics recovery is
unqualified in both projects today.

## Phase 4: offline installation

Goal: install macOS on the device from Recovery instead of assembling and
flashing a host-built userdata image.

Work items:

1. APFS container creation on UFS from Recovery (erase, create System/Data).
2. Run the paired installer (InstallAssistant/osinstallersetupd) against a
   local SharedSupport payload; install the System volume, seal it and create
   the cryptex volumes.
3. First-boot handoff: Data seed and root policies created on device; remove the
   host-side `userdata-root.img` step for fresh installs, keep fastboot images
   as a recovery path.
4. Record the install flow and its verification in `porting.toml`.

Acceptance: a fresh install from Recovery reaches the desktop without a
host-built root image; the existing flashed image stays bootable as fallback.

Risks: System volume sealing is a new authentication path compared with the
current owner-policy bypass.

## Phase 5: network installation and Apple-shaped boot picker

Goal: install and update macOS from Apple-hosted assets, with a graphical boot
picker.

Work items:

1. Asset fetch: `mobileassetd` against Apple CDN from Recovery, with the spoofed
   Mac17,11 identity; test whether Apple serves assets to a non-Apple board.
2. Installer download and resume, integrity checks, and rollback.
3. Graphical boot picker (OpenCanopy equivalent) with HID, persisted entries and
   a timeout policy.
4. Non-destructive updates and dual-boot preservation.

Acceptance: install from Apple CDN assets on a clean device; the boot picker
selects macOS or Android with persisted defaults.

Risks: Apple asset protocol and identity checks; terms of service; a failed
download must not leave the device unbootable.

## Verification and records

Each phase updates `porting.toml` findings and evidence with its code change,
records hardware results separately from build results, and keeps the run
notes in the workspace handoff. Recovery, install and identity work also needs
the paired IPSW inputs documented in the OS profile.

## References

- UniXNU documentation: https://unixnu.com/zh/porting/platform , https://unixnu.com/zh/porting/macos
- CrossDarwin macOS-ForSheng (recovery and native EL1 reference):
  https://github.com/CrossDarwin/macOS-ForSheng
- Snapintosh (EL2 monitor, install and APFS reference):
  https://github.com/Snapintosh/Snapintosh
- OpenCore PlatformInfo/Automatic boot entries (x86 analogue):
  https://dortania.github.io/OpenCore-Install-Guide/config.plist/