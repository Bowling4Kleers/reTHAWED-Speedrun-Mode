"""Read THAW zone .pak.wpc files (e.g. data/mod/Zones/z_mainmenu/z_mainmenu.pak.wpc).

Layout (as observed): a table of 32-byte entries from offset 0:
  +0 u32 extension checksum, +4 u32 data offset relative to the entry's own position, +8 u32 size,
  +12 u32 name checksum, +16..31 more checksums/flags.
The table ends at an entry whose extension is 0x2cb3ef3b ('last') or at a zero entry.
  python zonepak.py list <pak>
  python zonepak.py extract <pak> <outdir>     (files are named by index + name checksum; .qb containers are detected)
"""
import os, struct, sys, re

def entries(d):
    """+28 u32 flags: bit 0x20 = a 160-byte NUL-padded file name follows the 32-byte entry."""
    out = []; pos = 0
    while pos + 32 <= len(d):
        ext, off, size, name = struct.unpack('<4I', d[pos:pos+16])
        flags = struct.unpack('<I', d[pos+28:pos+32])[0]
        if ext == 0 or ext == 0x2cb3ef3b: break
        ln = 32 + (160 if flags & 0x20 else 0)
        fname = d[pos+32:pos+192].split(b'\0')[0].decode('latin1') if flags & 0x20 else ''
        out.append({'i': len(out), 'pos': pos, 'ext': ext, 'abs': pos + off, 'size': size, 'name': name,
                    'flags': flags, 'fname': fname, 'len': ln})
        pos += ln
    return out

def replace_file(d, index, blob):
    """Swap one file. Same size or smaller: in place (0xAB-filled tail). Bigger: appended after the last file
    (32-byte aligned, like the originals), with the entry's relative offset repointed; the pak is re-padded with 0xAB to 4096."""
    d = bytearray(d); es = entries(bytes(d)); e = es[index]
    if len(blob) <= e['size']:
        d[e['abs']:e['abs']+e['size']] = blob + b'\xab' * (e['size'] - len(blob))
    else:
        end = max(x['abs'] + x['size'] for x in es)
        at = (end + 31) // 32 * 32
        del d[at:]
        d += blob
        d[e['pos']+4:e['pos']+8] = struct.pack('<I', at - e['pos'])
        d += b'\xab' * ((-len(d)) % 4096)
    d[e['pos']+8:e['pos']+12] = struct.pack('<I', len(blob))
    return bytes(d)

def is_qb(blob):
    return len(blob) > 0x1c and blob[:4] == b'\0\0\0\0' and blob[8:0x1b] == bytes.fromhex('1c08020410040808' '0c0c08020414020404'[:0]) or (len(blob) > 0x1c and blob[8:12] == b'\x1c\x08\x02\x04')

if __name__ == '__main__':
    cmd, pak = sys.argv[1], sys.argv[2]
    d = open(pak, 'rb').read()
    es = entries(d)
    if cmd == 'list':
        for e in es:
            blob = d[e['abs']:e['abs']+e['size']]
            print('%4d pos=%6x abs=%8x size=%8d ext=%08x name=%08x %s %s' % (e['i'], e['pos'], e['abs'], e['size'], e['ext'], e['name'], 'QB' if is_qb(blob) else '', e['fname']))
    elif cmd == 'extract':
        out = sys.argv[3]; os.makedirs(out, exist_ok=True)
        for e in es:
            blob = d[e['abs']:e['abs']+e['size']]
            open(os.path.join(out, '%04d_%08x%s' % (e['i'], e['name'], '.qb' if is_qb(blob) else '.bin')), 'wb').write(blob)
