# Alienware M17x R2: 16GB RAM display fix (BIOS A10)

With two 8GB DDR3 SO-DIMMs installed, the BIOS Setup of the Alienware M17x R2 shows **Total Memory
0 MB, Memory Bank 0 0 MB, Memory Bank 1 0 MB**, and *Advanced → Performance Options* shows **Memory
Channel Mode: Single**. With 2×4GB everything displays correctly (4096 / 4096 / 8192 MB).

This project patches Dell's latest M17x R2 BIOS (**A10**) so Setup shows `8192 MB` / `8192 MB` /
`16384 MB` and *Dual*. Nothing else changes.

> **Status:** ✅ **the Setup fix is tested on real hardware.** An M17x R2 flashed with the patched A10
> shows Total Memory 16384 MB, Memory Bank 0/1 8192 MB and Memory Channel Mode *Dual* with 2×8GB.
> Flashing a BIOS always carries some risk. Proceed at your own risk.

> ⚠️ **8GB modules do not work with dual-core Arrandale CPUs** (e.g. i7-620M). This is not a BIOS
> display issue. With any 8GB DDR3 SO-DIMM, even a single one, an R2 with an i7-620M passes POST and
> shows the right size, but Windows crashes during boot (`STOP 0x1E`, all-zero parameters) and
> MemTest86+ resets within seconds. Two different 8GB kits that work perfectly in a Clarksfield M15x
> behave the same way.
>
> The BIOS's saved memory setup shows the modules ran at DDR3-1066 with correct timings, including
> the 4-Gbit refresh time (tRFC ≈ 262 ns). The most likely cause is that the Arrandale memory
> controller can't address 4-Gbit DRAM chips; Intel lists a maximum of 8GB (2×4GB) for these CPUs.
> **For 16GB, use a quad-core Clarksfield CPU** (i7-720QM/740QM/820QM/840QM/920XM), which the R2
> supports. A Clarksfield M15x runs these same modules at DDR3-1333.

> *Memory Frequency: `ERR`* is cosmetic and only appears with Arrandale CPUs. There, Setup can't read
> the Clarksfield memory-ratio register (`B0xFF:D3:F4` offset `0x50` returns `0xFF`), so it falls back
> to the modules' SPD rating, and DDR3-1600 (tCKmin `0x0A`) isn't in Dell's 1066/1333 list. With a
> Clarksfield CPU, Setup shows the actual frequency.

## Root cause

The BIOS is Phoenix SecureCore (Calpella, W851 board) with a legacy, CSM-based Setup. During POST,
module `_B08` (the SMBIOS Type 17 builder) saves each DIMM's size into a 3-bit CMOS token: `0x20A`
for bank 0 (CMOS `0x35`, bits 4-6) and `0x20D` for bank 1 (CMOS `0x43`, bits 0-2). Setup module
`_T00` reads those tokens back. Both sides use the same fixed table:

| Size (MB) | 0 | 512 | 1024 | 2048 | 4096 | **8192** |
|---|---|---|---|---|---|---|
| Code | 0 | 1 | 2 | 3 | 4 | *not handled* |

8192 MB is `0x2000`, which matches no entry, so the leftover low byte (`0x00`) is stored. Setup then
shows **0 MB**, and Memory Channel Mode, which only checks whether both codes are non-zero, shows
**Single**. The OS reads memory from the E820 map and SMBIOS, which were already correct, so it was
never affected.

The same bug exists in the Alienware M15x BIOS. See the companion project
[alienware-m15x-16gb-ram-fix](https://github.com/StevanZS/alienware-m15x-16gb-ram-fix).

## The patch

The patch replaces both lookup tables with arithmetic. Codes are stored the same way as before, and 8GB
becomes code 5, which fits in the 3-bit token:

| Module | Offset (decompressed) | New code |
|---|---|---|
| `_B08` POST | `0x2C20`, `0x2C56` | `bsf ax,ax; sub al,8`: MB → code (8192 → 5) |
| `_T00` Setup | `0x3B8A`, `0x3BCF` (Total Memory), `0x3C51` (Bank 0), `0x3CCE` (Bank 1) | `eax = 1 << (code+8)`: code → MB |

Every size the original handled (0 to 4096 MB) produces exactly the same result as before.

### Integrity

- Phoenix modules here are EFI-compressed. Recompressing **all 28 original modules** with the EDK
  `EfiCompress` reproduces Dell's bytes **bit-for-bit**, so the patched modules are packed exactly as
  Phoenix's build tool would pack them.
- Module sizes, offsets and headers are unchanged. The unused tail of each compressed stream is padded
  so each module's 8-bit checksum, and the file's 8-bit sum, are unchanged. The 32-bit file sum quoted
  in Dell's release notes (`2CB7 A8B3`) does change, because there isn't enough spare space to
  compensate for it.
- Only these two modules differ. The boot block, ME, descriptor, NVRAM and the EC firmware file are
  untouched.

## Getting the patched BIOS

The firmware is Dell/Phoenix copyrighted, so this repository contains no firmware. Download Dell's
`M17xR2A10.zip`, extract `A10/W851A10.ROM`, then:

```sh
pip install uefi_firmware
python patch_m17xr2.py W851A10.ROM out/
```

The script refuses to run unless the input matches the original ROM's SHA-256. Check the output
against [`SHA256SUMS.txt`](SHA256SUMS.txt). Patched builds may also be attached to
[Releases](../../releases).

### Flashing (recommended procedure)

Start from a laptop already on BIOS **A10 / EC 15.34**; Setup's Main page shows both. Boot plain DOS
(for example a FreeDOS USB stick made with Rufus) with Dell's `phlash16.exe` and the patched
`W851A10.ROM` on the stick, then run:

```
phlash16 /RO=BACKUP.ROM
phlash16 W851A10.ROM /mode=3 /s /v /exit
```

- `/RO` saves the current flash chip to `BACKUP.ROM`. Copy it somewhere safe before continuing. It
  holds your machine's own data, such as the Service Tag, and is what you'd write back with an SPI
  programmer if everything else fails.
- The flash command is Dell's `F.bat` command **without `/bbl`**, with `/v` added to verify each
  block. The boot block is identical in the patched image, so leaving out `/bbl` loses nothing and
  keeps Phoenix crisis recovery untouched even if the flash is interrupted.
- Don't run the rest of `F.bat` (`rpg` / `dflash`). The EC firmware is unchanged, and 15.34 is
  already installed.

Flash on AC power with a charged battery, and don't interrupt the flash. Power the laptop fully off
and on afterwards. Dell's `CRISDISK.ZIP` contains the original ROM as `BIOS.WPH` for crisis recovery.
Keep it.

## License

The patch script and documentation are [MIT](LICENSE) licensed. Dell/Phoenix firmware is not covered and remains its owners' property.
