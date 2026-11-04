"""Edit inside a decompressed QB script while keeping IF/ELSE skips right.

splice(s, i, j, new): s[i:j] = new, and every IF (0x47) / ELSE (0x48) whose skip range crosses position i is
moved by the size change. SWITCH branches (0x49) and inline-argument blocks (0x4A) crossing the edit are refused.
The size change must be a multiple of 4 (0x4A blocks are aligned relative to the script start, see HANDOFF v20).
"""
import struct

SIZES = {0x16: 4, 0x17: 4, 0x1a: 4, 0x1d: 8, 0x1e: 12, 0x1f: 8, 0x02: 4, 0x47: 2, 0x48: 2, 0x49: 2}

def tokens(s):
    """[(pos, token)] for the whole script; raises on a 0x4A block (layout not handled here)."""
    i = 0; out = []
    while i < len(s):
        t = s[i]; out.append((i, t)); i += 1
        if t in (0x1b, 0x1c):
            i += 4 + struct.unpack('<I', s[i:i+4])[0]
        elif t == 0x4a:
            raise ValueError('0x4A block at %d' % (i - 1))
        else:
            i += SIZES.get(t, 0)
    assert i == len(s)
    return out

def jumps(s):
    """[(u16 pos, target, token)]; target = u16 pos + skip"""
    return [(p + 1, p + 1 + struct.unpack('<H', s[p+1:p+3])[0], t) for p, t in tokens(s) if t in (0x47, 0x48, 0x49)]

def check(s):
    """every IF/ELSE skip must land right after an ELSE header or an ENDIF"""
    toks = tokens(s); ends = set()
    for p, t in toks:
        if t == 0x28: ends.add(p + 1)
        if t == 0x48: ends.add(p + 3)
    bad = [(a, b) for a, b, t in jumps(s) if t != 0x49 and b not in ends]
    assert not bad, 'bad jumps %r' % bad[:5]
    return s

def splice(s, i, j, new):
    check(s)
    delta = len(new) - (j - i)
    assert delta % 4 == 0, 'size change %d is not a multiple of 4' % delta
    s = bytearray(s)
    for a, b, t in jumps(bytes(s)):
        if a < i and b > i:
            assert t != 0x49, 'SWITCH branch crosses the edit at %d' % i
            assert b >= j, 'edit overlaps a jump target'
            s[a:a+2] = struct.pack('<H', b - a + delta)
    s[i:j] = new
    return check(bytes(s))

def find_stmt(s, pat, count=1):
    """position of pat, which must occur `count` times (first one used) and start a statement (follow a newline)"""
    assert s.count(pat) == count, (s.count(pat), count)
    i = s.index(pat); assert s[i-1] == 1, 'not at a statement start'
    return i
