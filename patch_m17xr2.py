"""Patch Alienware M17x R2 A10 BIOS so Setup shows 8GB DIMMs (Memory Bank 0/1, Total Memory, Channel Mode).

usage: python patch_m17xr2.py <Dell W851A10.ROM> <outdir>

Writes <outdir>/W851A10.ROM, which you flash with Dell's own F.bat (phlash16). Requires: pip install uefi_firmware
"""
import sys, os, re, hashlib
import uefi_firmware.efi_compressor as efi

ORIG_ROM_SHA256 = '77f130a76eb5d8c70be03da0b222f4d6cb90d3204aaf094999939dc9bc216d65'
rom_path, outdir = sys.argv[1:3]
os.makedirs(outdir, exist_ok=True)
rom = bytearray(open(rom_path, 'rb').read())
orig = bytes(rom)
if hashlib.sha256(orig).hexdigest() != ORIG_ROM_SHA256:
    raise SystemExit('input is not the original Dell W851A10.ROM (SHA-256 mismatch)')

# ---- code patches (offsets are within the decompressed module, incl. its 4-byte size header) ----
NOP = 0x90
# AL = size code -> EAX = size in MB (0 -> 0, n -> 1 << (n+8))
DECODE = bytes.fromhex(
    '660fb6c0'      # movzx eax, al
    '0ac0'          # or al, al
    '740d'          # jz  +13
    '0408'          # add al, 8
    '8ac8'          # mov cl, al
    '66b801000000'  # mov eax, 1
    '66d3e0')       # shl eax, cl
ADD_EBX_EAX = bytes.fromhex('6601c3')
# AX = size in MB -> AL = size code (0 / <512MB -> 0, 512 -> 1 ... 4096 -> 4, 8192 -> 5)
ENCODE = bytes.fromhex(
    '0fbcc0'        # bsf ax, ax
    '7404'          # jz  +4 (-> xor)
    '2c08'          # sub al, 8
    '7702'          # ja  +2 (-> done)
    '32c0')         # xor al, al

PATCHES = {
    '_T00': [  # (start, end, expected-first-bytes, expected-bytes-at-end, new code)
        (0x3b8a, 0x3bc4, '0ac07508', '668bd8', DECODE),                 # Total Memory: bank 0 part
        (0x3bcf, 0x3c0b, '0ac07506', '668bc3', DECODE + ADD_EBX_EAX),   # Total Memory: bank 1 part
        (0x3c51, 0x3c8b, '0bc07508', '8e4610', DECODE),                 # Memory Bank 0
        (0x3cce, 0x3d08, '0bc07508', '8e4610', DECODE),                 # Memory Bank 1
    ],
    '_B08': [
        (0x2c20, 0x2c4a, '0bc07504', '52ba0a02', ENCODE),               # POST: bank 0 -> token 0x20A
        (0x2c56, 0x2c80, '0bc07504', '52ba0d02', ENCODE),               # POST: bank 1 -> token 0x20D
    ],
}

def pad(code, n):
    assert len(code) <= n
    return code + bytes([NOP]) * (n - len(code))

def find_module(name):
    m = re.search(rb'\xf8\x00..(...)\x02' + re.escape(name.encode()), rom, re.S)
    assert m, name
    h = m.start()
    size = int.from_bytes(rom[h+4:h+7], 'little')
    for o in range(h + 12, h + 0x40):
        cs = int.from_bytes(rom[o:o+4], 'little'); us = int.from_bytes(rom[o+4:o+8], 'little')
        if rom[o-2:o] == b'\x00\x01' and 0 < cs < size and o + 8 + cs <= h + size:
            try:
                r = efi.EfiDecompress(bytes(rom[o:o+8+cs]), 8 + cs)
            except Exception:
                continue
            if len(r) == us:
                return h, size, o, cs, us, bytearray(r)
    raise SystemExit('module not found ' + name)

slots, report = [], []
for name, plist in PATCHES.items():
    h, size, o, cs, us, mod = find_module(name)
    for start, end, exp_head, exp_tail, code in plist:
        assert mod[start:start+len(bytes.fromhex(exp_head))].hex() == exp_head, (name, hex(start))
        assert mod[end:end+len(bytes.fromhex(exp_tail))].hex() == exp_tail, (name, hex(end))
        mod[start:end] = pad(code, end - start)
    # recompress with standard EFI compression (Dell/Phoenix used the same EDK compressor)
    comp = efi.EfiCompress(bytes(mod), len(mod))
    ncs = int.from_bytes(comp[0:4], 'little')
    assert int.from_bytes(comp[4:8], 'little') == us and len(comp) == 8 + ncs
    assert efi.EfiDecompress(comp, len(comp)) == bytes(mod)
    assert ncs < cs, f'{name}: recompressed {ncs:#x} does not fit slot {cs:#x}'
    # header CompSize and module size stay as they were; unused stream tail is free padding
    body = o + 8
    rom[body:body + cs] = comp[8:] + bytes(cs - ncs)
    slots.append((name, h, size, o, cs, body + ncs, body + cs, bytes(mod)))
    report.append(f'{name}: module@{h:#x} stream@{o:#x} slot {cs:#x}, recompressed {ncs:#x} ({cs-ncs} spare)')

# fill the spare tails so (a) each module's 8-bit checksum is unchanged, and (b) if possible the
# whole-file 32-bit byte sum (quoted in Dell's release notes) is unchanged too
need = sum(orig) - sum(rom)
spare = [(name, a, b) for name, h, size, o, cs, a, b, mod in slots]
total_spare = sum(b - a for _, a, b in spare)
keep_sum32 = 0 <= need <= 255 * total_spare
for name, h, size, o, cs, a, b, mod in slots:
    mod_need = (sum(orig[h:h+size]) - sum(rom[h:h+size])) & 0xff   # what this module must add mod 256
    if keep_sum32:
        share = min(need, 255 * (b - a))
        share += (mod_need - share) & 0xff if share + ((mod_need - share) & 0xff) <= 255 * (b - a) else -((share - mod_need) & 0xff)
    else:
        share = mod_need
    for i in range(a, b):
        v = min(255, share); rom[i] = v; share -= v
    assert share == 0, name
    need -= sum(rom[a:b])
    assert sum(rom[h:h+size]) & 0xff == 0xf8, name           # module checksum (byte h+3 unchanged)
    assert (sum(rom[h:h+0x18]) - rom[h+3]) & 0xff == 0xf8, name  # header checksum (byte h+2 unchanged)
    # decode exactly as the BIOS will: original CompSize, padded tail included
    assert efi.EfiDecompress(bytes(rom[o:o+8+cs]), 8 + cs) == mod, name

diff = [i for i in range(len(rom)) if rom[i] != orig[i]]
assert all(any(h <= i < h + size for _, h, size, *_ in slots) for i in diff), 'change outside target modules'
assert sum(rom) & 0xff == sum(orig) & 0xff

open(os.path.join(outdir, 'W851A10.ROM'), 'wb').write(rom)
print('\n'.join(report))
print('changed bytes:', len(diff), 'range', hex(diff[0]), '-', hex(diff[-1]))
print('file sum32:', hex(sum(orig) & 0xffffffff), '->', hex(sum(rom) & 0xffffffff),
      '(preserved)' if sum(rom) == sum(orig) else '(8-bit sum preserved only)')
print('W851A10.ROM', hashlib.sha256(rom).hexdigest())
