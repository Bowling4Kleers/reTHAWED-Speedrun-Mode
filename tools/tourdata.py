"""Classic tour facts read from the qb files at build time.

tours(files) -> [ {id, level_list, levels: [load checksum], comps: [load checksum]} ] in registration order, from the RTCM_RegisterTour
calls in classic_tours.qb ($154b5e5b). A level entry with $9b93be3b = 1 is a competition (medals, no goals). Its `level` is a level
struct global from levels.qb, whose $893d9170 field is the load checksum the level select / GetCurrentLevel use.
"""
import os, struct, zlib
from qbp import items, script_at
from qbsplice import tokens

h = lambda s: (~zlib.crc32(s.lower().encode('latin1'))) & 0xffffffff
REGISTER = h('RTCM_RegisterTour'); ID = h('id'); LEVEL_LIST = h('level_list'); LEVEL = h('level'); COMP = 0x9b93be3b
LOAD = 0x893d9170

def _names(s):
    """[(kind, value)] tokens: ('n', hash) names, ('i', int), ('o', op byte)"""
    out = []
    for p, t in tokens(s):
        if t == 0x16: out.append(('n', struct.unpack('<I', s[p+1:p+5])[0]))
        elif t == 0x17: out.append(('i', struct.unpack('<i', s[p+1:p+5])[0]))
        else: out.append(('o', t))
    return out

def _struct_fields(d, p):
    """{name: value} of a type-10 struct global item at p (scalar fields only)"""
    s = struct.unpack('<I', d[p+12:p+16])[0]
    q = struct.unpack('<I', d[s+4:s+8])[0]; out = {}
    while q:
        fl, nm, val, nx = struct.unpack('<4I', d[q:q+16]); out[nm] = val; q = nx
    return out

def tours(files):
    ct = open(os.path.join(files, 'scripts', 'game', 'goals', 'classic_tours.qb.wpc'), 'rb').read()
    s = script_at(ct, next(p for p, t, n in items(ct) if t == 7 and n == 0x154b5e5b))
    lv = open(os.path.join(files, 'scripts', 'game', 'levels.qb.wpc'), 'rb').read()
    load = {n: _struct_fields(lv, p).get(LOAD) for p, t, n in items(lv) if t == 10}
    toks = _names(s); out = []; depth = 0; cur = None; entry = None
    for k, (kind, v) in enumerate(toks):
        nxt = lambda j: toks[k + j] if k + j < len(toks) else (None, None)
        if kind == 'n' and v == REGISTER:
            cur = {'id': None, 'level_list': None, 'levels': [], 'comps': []}; out.append(cur); depth = 0; continue
        if cur is None: continue
        if kind == 'o' and v in (3, 5): depth += 1
        elif kind == 'o' and v in (4, 6):
            depth -= 1
            if depth == 2 and entry is not None:
                if entry.get('level') is not None:
                    ld = load[entry['level']]
                    cur['levels'].append(ld)
                    if entry.get('comp') == 1: cur['comps'].append(ld)
                entry = None
        if kind == 'o' and v == 3 and depth == 3: entry = {}
        if kind == 'n' and nxt(1) == ('o', 7):
            val = nxt(2)
            if depth == 1 and v == ID: cur['id'] = val[1]
            elif depth == 1 and v == LEVEL_LIST: cur['level_list'] = val[1]
            elif depth == 3 and entry is not None and v == LEVEL: entry['level'] = val[1]
            elif depth == 3 and entry is not None and v == COMP: entry['comp'] = val[1]
    return out

if __name__ == '__main__':
    import sys, pickle
    D = pickle.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dict.pkl'), 'rb'))
    for t in tours(sys.argv[1]):
        print(D.get(t['id'], hex(t['id'])), hex(t['level_list'] or 0), len(t['levels']), 'levels, comps:', [hex(x) for x in t['comps']])
