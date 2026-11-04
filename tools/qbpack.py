#!/usr/bin/env python3
"""Unpack / repack ReTHAWed qb.pak.wpc + qb.pab.wpc (verified byte-exact round trip on 5.0.0.2).

  python qbpack.py unpack  <qb.pak.wpc> <qb.pab.wpc> <dir>
  python qbpack.py pack    <orig qb.pak.wpc> <dir> <out qb.pak.wpc> <out qb.pab.wpc>

Layout: pak = 192-byte entries (+8 = file size, +32 = NUL-terminated name). pab = files in index order,
each starting on a 32-byte boundary (zero padded), then 0xAB fill up to a 4096 multiple.
The pak size (+8) and offset (+4 = len(pak)+pab_pos-192*k) columns are recomputed, so files may grow or shrink.
"""
import sys, os, struct

ENT = 192
def entries(pak):
    """(entry pos, name, size, k) for each file. Entries are 32 bytes, plus a 160-byte name when flags (+28) & 0x20.
    5.0.0.2 release paks name every entry; the newer SDK-built paks leave some unnamed (those get a hash path)."""
    out = []; pos = 0; k = 0
    while pos + 32 <= len(pak):
        ext, off, size, fh, _, _, _, fl = struct.unpack('<8I', pak[pos:pos+32])
        if ext == 0 and off == 0: break
        if ext == 0xb524565f: break          # 'last' terminator
        if fl & 0x20:
            name = pak[pos+32:pos+192].split(b'\0')[0].decode('latin1').replace('\\', '/'); L = 192
        else:
            name = '_unnamed/%08x_%08x.bin' % (ext, fh); L = 32
        out.append((pos, name, size, k)); pos += L; k += 1
    return out

def align(n, a): return (n + a - 1) // a * a

def unpack(pakp, pabp, outdir):
    pak = open(pakp, 'rb').read(); pab = open(pabp, 'rb').read()
    pos = 0
    for e, name, size, _k in entries(pak):
        pos = align(pos, 32)
        path = os.path.join(outdir, name.replace('/', os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'wb').write(pab[pos:pos+size]); pos += size
    print('unpacked; end of data at', pos, 'of', len(pab))

def pack(origpak, indir, outpak, outpab, tail=None):
    pak = bytearray(open(origpak, 'rb').read()); pab = bytearray(); k = 0
    for e, name, _, _k in entries(pak):
        d = open(os.path.join(indir, name.replace('/', os.sep)), 'rb').read()
        pab += b'\0' * (align(len(pab), 32) - len(pab))
        # offset column = len(pak) + position in pab - 192*k  (verified for all 501 entries)
        pak[e+4:e+8] = struct.pack('<I', len(pak) + len(pab) - e)
        pab += d
        pak[e+8:e+12] = struct.pack('<I', len(d))
        k += 1
    pab += b'\0' * (align(len(pab), 32) - len(pab))
    pab += b'\xab' * (align(len(pab), 4096) - len(pab))
    open(outpak, 'wb').write(pak); open(outpab, 'wb').write(pab)
    print('packed', len(pak), len(pab))

def pack_stable(origpak, origpab, indir, outpak, outpab):
    """Like pack, but every file stays at its original pab offset; a file that grew is moved to the end of the pab."""
    pak = bytearray(open(origpak, 'rb').read()); pab = bytearray(open(origpab, 'rb').read())
    end = 0; pos = 0; grown = []
    for k, (e, name, size, _k) in enumerate(entries(pak)):
        pos = align(pos, 32); end = pos + size
        d = open(os.path.join(indir, name.replace('/', os.sep)), 'rb').read()
        if len(d) <= size:
            pab[pos:pos+size] = d + b'\0' * (size - len(d))
            pak[e+8:e+12] = struct.pack('<I', len(d))
        else:
            grown.append((k, e, name, d))
        pos = end
    del pab[align(end, 32):]
    for k, e, name, d in grown:
        pab += b'\0' * (align(len(pab), 32) - len(pab))
        pak[e+4:e+8] = struct.pack('<I', len(pak) + len(pab) - e)
        pak[e+8:e+12] = struct.pack('<I', len(d))
        pab += d
        print('moved to end:', name)
    pab += b'\0' * (align(len(pab), 32) - len(pab))
    pab += b'\xab' * (align(len(pab), 4096) - len(pab))
    open(outpak, 'wb').write(pak); open(outpab, 'wb').write(pab)
    print('packed (stable)', len(pak), len(pab))

if __name__ == '__main__':
    a = sys.argv[1:]
    if a[:1] == ['unpack']: unpack(*a[1:4])
    elif a[:1] == ['pack']: pack(*a[1:5])
    elif a[:1] == ['pack_stable']: pack_stable(*a[1:6])
    else: print(__doc__)
