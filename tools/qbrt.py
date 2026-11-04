"""qbrt: lossless QB decompile / recompile for ReTHAWed (THAW) script containers.

  python -I qbrt.py decompile <qb.pak.wpc> <qb.pab.wpc> <src dir>     every .qb.wpc -> <src dir>/<path>.qbs
  python -I qbrt.py build <qb.pak.wpc> <qb.pab.wpc> <src dir> <out pak> <out pab>
  python -I qbrt.py dis <file.qb.wpc> <out.qbs>                        one container
  python -I qbrt.py asm <orig file.qb.wpc> <in.qbs> <out.qb.wpc>
  python -I qbrt.py zdecompile <zone.pak.wpc> <src dir>                 QB files inside a zone pak (e.g. z_mainmenu)
  python -I qbrt.py zbuild <zone.pak.wpc> <src dir> <out zone.pak.wpc>
  python -I qbrt.py verify <qb.pak.wpc> <qb.pab.wpc>                   round-trip every script and an unedited build

Round trip guarantees
  - The text form of a script assembles back to exactly the same decompressed bytes (verify checks all of them).
  - build only touches scripts whose text changed. Every other script item, data item and file keeps its stored bytes,
    and an unedited source tree rebuilds qb.pak/qb.pab byte for byte.
  - A changed script is re-compressed into its own slot when it fits (nothing else moves); otherwise the old item is
    renamed rt_old_<hash> and the new one appended to the container (as build_speedrun_v1 does). A container that grows
    is moved to the end of qb.pab and every other file keeps its offset (PACK=stable).
  - The assembler recomputes IF/ELSE skips, jump labels and the 4-byte padding before argument blocks, so edits of
    any size are safe. It refuses the >= token (it hangs this engine, see HANDOFF).

Text format (.qbs)
  === script <name>        a script item; its body runs to the next line starting with '=== '
  === data <name> ...      a data item (globals, structs, arrays), shown read-only; its lines are ignored by build
  A newline in the body is the engine's end-of-line token, so keep statements on their lines. Indentation, spaces and
  // comments are ignored. Trailing blank lines at the end of a body are ignored too.
  Names: plain names hash to ~crc32(lowercase), $1234abcd is a raw checksum. <name> reads a parameter, <...> passes all.
  Literals: 12, 1.5, "text", L"text" (local string), W"text" (wide string), pair(1.0,2.0), vec(1.0,2.0,3.0), 0x1F (hex int).
  IF / ELSE / ENDIF, BEGIN / REPEAT / BREAK, SWITCH / CASE / DEFAULT / ENDSWITCH, RETURN, NOT, AND, OR, ENDSCRIPT.
  Jumps the compiler emitted are shown with labels: @@L3 marks a spot, SKIP @L3 / JUMP @L3 / RANDOM{1:@L3 1:@L4}
  jump to it (SKIP is the u16 jump used by SWITCH cases). IF @L3 / ELSE @L3 appear only where a skip is not the usual one.
  ARGS{ name = value ... } is an inline argument block (0x4A); values: 1, 1.0, "s", L"s", name, { ... }, [#0a ... ]
  (array, element type in hex), pair(), vec(), raw:TT:0xVALUE. ARGS#<hex> is a block shown as raw bytes.
  %xx is a bare token with no readable name, #xx:<hex> a token with its operand bytes, @line:N a line-number token.
Unverified names (byte values are exact either way): 0x32 is shown as OR and 0x33 as AND (THUG naming).
"""
import os, sys, re, struct, zlib, pickle, hashlib
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
from qbp import items, script_at
from qbcomp import compress, compress_exact
import zonepak

def h(s): return (~zlib.crc32(s.lower().encode('latin1'))) & 0xffffffff

_D = None
def names():
    global _D
    if _D is None:
        _D = {}
        try: raw = pickle.load(open(os.path.join(here, 'dict.pkl'), 'rb'))
        except OSError: raw = {}
        extra = os.path.join(here, 'build_speedrun_v1.py')
        if os.path.exists(extra):
            for m in re.finditer(r"['\"]([A-Za-z_][A-Za-z0-9_]{2,40})['\"]", open(extra, encoding='utf8').read()):
                raw.setdefault(h(m.group(1)), m.group(1))
        for k, v in raw.items():
            if isinstance(v, str) and IDENT.fullmatch(v) and h(v) == k and v not in KEYWORDS and not RESERVED.fullmatch(v):
                _D[k] = v
    return _D

IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
RESERVED = re.compile(r'(?i)(pair|vec|enum|raw|args|L|W)')
def nm(k): return names().get(k) or '$%08x' % k

# ---------------------------------------------------------------- tokens
OPS = {0x01: '\n', 0x03: '{', 0x04: '}', 0x05: '[', 0x06: ']', 0x07: '=', 0x08: '.', 0x09: ',', 0x0a: '-', 0x0b: '+',
       0x0c: '/', 0x0d: '*', 0x0e: '(', 0x0f: ')', 0x11: '==', 0x12: '<', 0x13: '<=', 0x14: '>', 0x15: '>=',
       0x20: 'BEGIN', 0x21: 'REPEAT', 0x22: 'BREAK', 0x23: 'SCRIPT', 0x24: 'ENDSCRIPT', 0x28: 'ENDIF', 0x29: 'RETURN',
       0x2a: 'UNDEFINED', 0x2c: '<...>', 0x30: 'RANDOMRANGE', 0x32: 'OR', 0x33: 'AND', 0x34: 'XOR', 0x35: 'SHL', 0x36: 'SHR',
       0x39: 'NOT', 0x3c: 'SWITCH', 0x3d: 'ENDSWITCH', 0x3e: 'CASE', 0x3f: 'DEFAULT', 0x42: ':'}
OPB = {v: k for k, v in OPS.items()}
RANDOMS = {0x2f: 'RANDOM', 0x40: 'RANDOM_NOREPEAT', 0x41: 'RANDOM_PERMUTE'}
RANDB = {v: k for k, v in RANDOMS.items()}
KEYWORDS = set(v for v in OPS.values() if v.isalpha() or '_' in v) | set(RANDOMS.values()) | {'IF', 'ELSE', 'SKIP', 'JUMP'}
FIXED = {0x16: 4, 0x17: 4, 0x18: 4, 0x19: 4, 0x1a: 4, 0x1d: 8, 0x1e: 12, 0x1f: 8, 0x02: 4, 0x2e: 4, 0x47: 2, 0x48: 2, 0x49: 2}

def tokens(s):
    """[(start, op, end)]; raises ValueError if the stream does not tokenize to its exact end."""
    i = 0; out = []; n = len(s)
    while i < n:
        o = s[i]; st = i; i += 1
        if o in (0x1b, 0x1c, 0x4c): i += 4 + struct.unpack_from('<I', s, i)[0]
        elif o == 0x4a:
            k = struct.unpack_from('<H', s, i)[0]; i += 2; i += (-i) % 4 + k
        elif o in RANDOMS:
            c = struct.unpack_from('<I', s, i)[0]; i += 4 + 6 * c
        elif o == 0x2b:
            i += 4; i = s.index(b'\0', i) + 1
        else: i += FIXED.get(o, 0)
        if i > n: raise ValueError('token 0x%02x at %d overruns the script' % (o, st))
        out.append((st, o, i))
    return out

def jump_targets(s, T):
    """{token start: [target, ...]} for SKIP/JUMP/RANDOM tokens."""
    res = {}
    for st, o, e in T:
        if o == 0x49: res[st] = [st + 1 + struct.unpack_from('<H', s, st + 1)[0]]
        elif o == 0x2e: res[st] = [st + 5 + struct.unpack_from('<i', s, st + 1)[0]]
        elif o in RANDOMS:
            c = struct.unpack_from('<I', s, st + 1)[0]; b = st + 5 + 2 * c
            res[st] = [b + 4 * j + 4 + struct.unpack_from('<i', s, b + 4 * j)[0] for j in range(c)]
    return res

def canonical_ifs(s, T):
    stack = []
    for st, o, e in T:
        if o == 0x47: stack.append(st + 1)
        elif o in (0x48, 0x28):
            if not stack: return False
            u = stack.pop()
            if u + struct.unpack_from('<H', s, u)[0] != e: return False
            if o == 0x48: stack.append(st + 1)
    return not stack

# ---------------------------------------------------------------- literals
def fmt_float(b):
    v = struct.unpack('<f', b)[0]
    if v != v or v in (float('inf'), float('-inf')): return None
    for p in range(1, 10):
        t = '%.*g' % (p, v)
        if struct.pack('<f', float(t)) == b: break
    else: return None
    if not re.search(r'[.eE]', t): t += '.0'
    return t

def fmt_str(b, wide=False):
    if wide:
        if len(b) % 2 or not b.endswith(b'\0\0'): return None
        try: txt = b[:-2].decode('utf-16-be')
        except UnicodeDecodeError: return None
        if '\0' in txt: return None
        out = ''.join(c if 32 <= ord(c) < 127 and c not in '"\\' else '\\' + c if c in '"\\' else '\\u%04x' % ord(c) for c in txt)
        r = 'W"%s"' % out
    else:
        if not b.endswith(b'\0') or b'\0' in b[:-1]: return None
        out = ''.join(chr(c) if 32 <= c < 127 and c not in (34, 92) else '\\' + chr(c) if c in (34, 92) else '\\x%02x' % c for c in b[:-1])
        r = '"%s"' % out
    return r

def parse_str(lex):
    pre = lex[0] if lex[0] in 'LW' else ''; body = lex[len(pre) + 1:-1]
    out = []; i = 0
    while i < len(body):
        c = body[i]
        if c == '\\':
            d = body[i + 1]
            if d == 'x': out.append(chr(int(body[i+2:i+4], 16))); i += 4; continue
            if d == 'u': out.append(chr(int(body[i+2:i+6], 16))); i += 6; continue
            if d == 'n': out.append('\n'); i += 2; continue
            if d == 't': out.append('\t'); i += 2; continue
            out.append(d); i += 2; continue
        out.append(c); i += 1
    txt = ''.join(out)
    if pre == 'W': return pre, txt.encode('utf-16-be') + b'\0\0'
    return pre, txt.encode('latin1') + b'\0'

# ---------------------------------------------------------------- inline structs (0x4A) and data items
DIRECT = {1, 2, 13}            # array element types stored in the offset table itself
class Fail(Exception): pass

def dec_struct(b, off, base=0):
    fl, first = struct.unpack_from('<2I', b, off)
    if fl != 0x10000: raise Fail('struct flags')
    comps = []; q = first; guard = 0
    while q:
        q -= base
        if q < 0 or q + 16 > len(b) or guard > 4096: raise Fail('component offset')
        f, name, v, nx = struct.unpack_from('<4I', b, q); t = (f >> 8) & 0xff
        if f & ~0xff00: raise Fail('component flags')
        comps.append((name, t, dec_value(b, t >> 1, v, base) if t & 1 else ('raw', v))); q = nx; guard += 1
    return ('struct', comps)

def cstr(b, p, wide=False):
    if wide:
        j = p
        while b[j:j+2] != b'\0\0': j += 2
        return b[p:j+2]
    return b[p:b.index(b'\0', p) + 1]

def dec_value(b, tc, v, base):
    if tc == 1: return ('int', struct.unpack('<i', struct.pack('<I', v))[0])
    if tc == 2: return ('float', struct.pack('<I', v))
    if tc == 13: return ('name', v)
    p = v - base
    if tc in (3, 4): return ('str' if tc == 3 else 'lstr', cstr(b, p))
    if tc in (5, 6):
        hdr = struct.unpack_from('<I', b, p)[0]
        if hdr != 0x100: raise Fail('pair/vec header')
        return ('pair' if tc == 5 else 'vec', b[p+4:p+4+(8 if tc == 5 else 12)])
    if tc == 10: return dec_struct(b, p, base)
    if tc == 12: return dec_array(b, p, base)
    return ('raw', v)

def dec_array(b, p, base):
    hdr, cnt, tbl = struct.unpack_from('<3I', b, p)
    et = (hdr >> 16) & 0xff
    if hdr & 0xff00ffff != 0x100: raise Fail('array header')
    if cnt == 0: return ('array', et, [], tbl)
    tb = tbl - base; els = []
    if cnt == 1 and et not in DIRECT:      # a single element is pointed at directly
        return ('array', et, [dec_value(b, et, tbl, base)], None)
    for j in range(cnt):
        e = struct.unpack_from('<I', b, tb + 4 * j)[0]
        els.append(dec_value(b, et, e, base))
    return ('array', et, els, None)

class Enc:
    def __init__(s, base=0): s.out = bytearray(); s.base = base
    def pos(s): return s.base + len(s.out)
    def pad(s): s.out += b'\0' * ((-len(s.out)) % 4)
    def struct_(s, comps):
        st = len(s.out); s.out += b'\0' * 8
        struct.pack_into('<2I', s.out, st, 0x10000, s.base + st + 8 if comps else 0)
        for k, (name, t, val) in enumerate(comps):
            q = len(s.out); s.out += b'\0' * 16
            v = val[1] if val[0] == 'raw' else s.value(t >> 1, val)
            s.pad()
            struct.pack_into('<4I', s.out, q, t << 8, name, v, s.pos() if k + 1 < len(comps) else 0)
        return s.base + st
    def value(s, tc, val):
        k = val[0]
        if k == 'int': return val[1] & 0xffffffff
        if k == 'float': return struct.unpack('<I', val[1])[0]
        if k == 'name': return val[1]
        at = s.pos()
        if k in ('str', 'lstr'): s.out += val[1]; s.pad(); return at
        if k in ('pair', 'vec'): s.out += struct.pack('<I', 0x100) + val[1]; return at
        if k == 'struct': return s.struct_(val[1])
        if k == 'array': return s.array(val)
        raise Fail(k)
    def array(s, val):
        _, et, els, tbl0 = val; st = len(s.out); s.out += b'\0' * 12
        if not els:
            struct.pack_into('<3I', s.out, st, 0x100 | et << 16, 0, tbl0 or 0); return s.base + st
        if len(els) == 1 and et not in DIRECT:
            v = s.value(et, els[0]); struct.pack_into('<3I', s.out, st, 0x100 | et << 16, 1, v); return s.base + st
        tb = len(s.out); s.out += b'\0' * (4 * len(els))
        struct.pack_into('<3I', s.out, st, 0x100 | et << 16, len(els), s.base + tb)
        for j, e in enumerate(els):
            v = s.value(et, e); s.pad(); struct.pack_into('<I', s.out, tb + 4 * j, v)
        return s.base + st

TYPE_OF = {'int': 1, 'float': 2, 'str': 3, 'lstr': 4, 'pair': 5, 'vec': 6, 'struct': 10, 'array': 12, 'name': 13}

def fmt_val(val):
    k = val[0]
    if k == 'int': return str(val[1])
    if k == 'float':
        f = fmt_float(val[1])
        if f is None: raise Fail('float')
        return f
    if k == 'name': return nm(val[1])
    if k == 'str' or k == 'lstr':
        f = fmt_str(val[1])
        if f is None: raise Fail('string')
        return ('L' if k == 'lstr' else '') + f
    if k in ('pair', 'vec'):
        fs = [fmt_float(val[1][i:i+4]) for i in range(0, len(val[1]), 4)]
        if None in fs: raise Fail('float')
        return '%s(%s)' % (k, ','.join(fs))
    if k == 'struct': return fmt_struct(val[1])
    if k == 'array':
        if val[3]: raise Fail('empty array with table offset')
        return '[#%02x %s]' % (val[1], ' '.join(fmt_val(e) for e in val[2]) + (' ' if val[2] else ''))
    raise Fail(k)

def fmt_struct(comps):
    parts = []
    for name, t, val in comps:
        if not t & 1 or val[0] == 'raw':
            v = 'raw:%02x:0x%08x' % (t, val[1])
        else:
            v = fmt_val(val)
            if TYPE_OF[val[0]] != t >> 1: raise Fail('type mismatch')
        parts.append(('%s = %s' % (nm(name), v)) if name else v)
    return '{ ' + ' '.join(parts) + (' }' if parts else '}')

def fmt_args(blk):
    try:
        d = dec_struct(blk, 0)
        e = Enc(); e.struct_(d[1])
        if bytes(e.out) != blk: raise Fail('layout')
        return 'ARGS' + fmt_struct(d[1])
    except (Fail, struct.error, ValueError, IndexError, KeyError):
        return 'ARGS#' + blk.hex()

# ---------------------------------------------------------------- disassembler
INDENT_OPEN = {0x47, 0x20, 0x3c, 0x03, 0x05}; INDENT_CLOSE = {0x28, 0x21, 0x3d, 0x04, 0x06}

def disassemble(s):
    """script bytes -> text that assemble() turns back into the same bytes"""
    try: T = tokens(s)
    except (ValueError, struct.error): return 'RAWSCRIPT#' + s.hex()
    starts = {st for st, o, e in T} | {len(s)}
    J = jump_targets(s, T)
    if any(t not in starts for ts in J.values() for t in ts): return 'RAWSCRIPT#' + s.hex()
    canon = canonical_ifs(s, T)
    if not canon:
        for st, o, e in T:
            if o in (0x47, 0x48): J[st] = [st + 1 + struct.unpack_from('<H', s, st + 1)[0]]
        if any(t not in starts for ts in J.values() for t in ts): return 'RAWSCRIPT#' + s.hex()
    lab = {t: 'L%d' % (k + 1) for k, t in enumerate(sorted({t for ts in J.values() for t in ts}))}
    out = []; depth = 0; line_start = True; skip = set()
    # trailing newline tokens would be stripped as blank lines, so they are written as %01
    last_real = len(T)
    while last_real and T[last_real - 1][1] == 0x01: last_real -= 1
    for idx, (st, o, e) in enumerate(T):
        if idx in skip: continue
        if st in lab: out.append('@@' + lab[st])
        if o == 0x01:
            if idx >= last_real: out.append('%01'); continue
            out.append('\n'); line_start = True; continue
        if line_start:
            d = depth - (1 if o in INDENT_CLOSE or o == 0x48 else 0)
            out.append('    ' * max(d, 0)); line_start = False
        if o in INDENT_OPEN: depth += 1
        elif o in INDENT_CLOSE: depth = max(depth - 1, 0)
        a = s[st+1:e]
        if o == 0x16: out.append(nm(struct.unpack('<I', a)[0]))
        elif o == 0x2d and idx + 1 < len(T) and T[idx+1][1] == 0x16 and T[idx+1][0] not in lab:
            out.append('<%s>' % nm(struct.unpack_from('<I', s, T[idx+1][0] + 1)[0])); skip.add(idx + 1)
        elif o == 0x17: out.append(str(struct.unpack('<i', a)[0]))
        elif o == 0x18: out.append('0x%X' % struct.unpack('<I', a)[0])
        elif o == 0x19: out.append('enum:%d' % struct.unpack('<i', a)[0])
        elif o == 0x02: out.append('@line:%d' % struct.unpack('<I', a)[0])
        elif o == 0x1a:
            f = fmt_float(a); out.append(f if f else '#1a:' + a.hex())
        elif o in (0x1f, 0x1e):
            fs = [fmt_float(a[i:i+4]) for i in range(0, len(a), 4)]
            out.append(('pair(%s)' if o == 0x1f else 'vec(%s)') % ','.join(fs) if None not in fs else '#%02x:%s' % (o, a.hex()))
        elif o in (0x1b, 0x1c, 0x4c):
            f = fmt_str(a[4:], wide=(o == 0x4c))
            if f and o == 0x1c: f = 'L' + f
            out.append(f if f else '#%02x:%s' % (o, a.hex()))
        elif o == 0x4a:
            k = struct.unpack('<H', a[:2])[0]; out.append(fmt_args(s[e-k:e]))
        elif o in (0x47, 0x48):
            out.append(('IF' if o == 0x47 else 'ELSE') + ('' if canon else ' @' + lab[J[st][0]]))
        elif o == 0x49: out.append('SKIP @' + lab[J[st][0]])
        elif o == 0x2e: out.append('JUMP @' + lab[J[st][0]])
        elif o in RANDOMS:
            c = struct.unpack('<I', a[:4])[0]; w = struct.unpack('<%dH' % c, a[4:4+2*c])
            out.append('%s{%s}' % (RANDOMS[o], ' '.join('%d:@%s' % (w[j], lab[J[st][j]]) for j in range(c))))
        elif o in OPS and not a: out.append(OPS[o])
        elif not a: out.append('%%%02x' % o)
        else: out.append('#%02x:%s' % (o, a.hex()))
    if len(s) in lab: out.append('@@' + lab[len(s)])
    text = ''
    for t in out:
        if t == '\n': text = text.rstrip(' ') + '\n'
        elif t.startswith(' ') or t == '' or not t.strip(): text += t
        else: text += ('' if not text or text.endswith((' ', '\n')) else ' ') + t
    return text

# ---------------------------------------------------------------- assembler
LEX = re.compile(r'''
  (?P<nl>\n) | (?P<ws>[ \t\r]+) | (?P<com>//[^\n]*)
| (?P<raws>RAWSCRIPT\#[0-9a-fA-F]*) | (?P<rawa>ARGS\#[0-9a-fA-F]*) | (?P<rawt>\#[0-9a-fA-F]{2}:[0-9a-fA-F]*)
| (?P<args>ARGS\{) | (?P<rand>(?:RANDOM|RANDOM_NOREPEAT|RANDOM_PERMUTE)\{[^}]*\})
| (?P<str>[LW]?"(?:[^"\\\n]|\\.)*")
| (?P<ldef>@@L\d+) | (?P<lref>@L\d+) | (?P<line>@line:\d+)
| (?P<pv>(?:pair|vec)\([^)]*\)) | (?P<enum>enum:-?\d+) | (?P<raw>raw:[0-9a-fA-F]{2}:0x[0-9a-fA-F]{1,8})
| (?P<arr>\[\#[0-9a-fA-F]{2}) | (?P<bare>%[0-9a-fA-F]{2})
| (?P<hex>0x[0-9a-fA-F]+) | (?P<num>-?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)
| (?P<arg><(?:[A-Za-z_][A-Za-z0-9_]*|\$[0-9a-fA-F]{8})>) | (?P<allargs><\.\.\.>)
| (?P<name>[A-Za-z_][A-Za-z0-9_]*|\$[0-9a-fA-F]{8})
| (?P<op>==|<=|>=|[{}\[\]=.,\-+/*()<>:])
''', re.X)

class AsmError(Exception): pass

def lex(text):
    out = []; i = 0; line = 1
    while i < len(text):
        m = LEX.match(text, i)
        if not m: raise AsmError('line %d: cannot read %r' % (line, text[i:i+20]))
        k = m.lastgroup; v = m.group()
        if k == 'nl': line += 1
        if k not in ('ws', 'com'): out.append((k, v, line))
        i = m.end()
    while out and out[-1][0] == 'nl': out.pop()
    return out

def hashof(n): return int(n[1:], 16) if n.startswith('$') else h(n)

def num_tok(v):
    if re.search(r'[.eE]', v) and not v.lower().startswith('0x'): return b'\x1a' + struct.pack('<f', float(v))
    n = int(v)
    if not -2**31 <= n < 2**32: raise AsmError('integer out of range: ' + v)
    return b'\x17' + struct.pack('<I', n & 0xffffffff)

def parse_value(L, i, et=None):
    """inline-struct value at L[i] -> (value tuple or ('raw', t, v), next i)"""
    k, v, ln = L[i]
    if k == 'num':
        if re.search(r'[.eE]', v): return ('float', struct.pack('<f', float(v))), i + 1
        return ('int', int(v)), i + 1
    if k == 'str':
        pre, b = parse_str(v)
        if pre == 'W': raise AsmError('line %d: wide strings are not allowed in ARGS' % ln)
        return ('lstr' if pre == 'L' else 'str', b), i + 1
    if k == 'name': return ('name', hashof(v)), i + 1
    if k == 'pv':
        kind = v[:v.index('(')]; fs = [float(x) for x in v[v.index('(')+1:-1].split(',')]
        if len(fs) != (2 if kind == 'pair' else 3): raise AsmError('line %d: %s needs %d numbers' % (ln, kind, 2 if kind == 'pair' else 3))
        return (kind, struct.pack('<%df' % len(fs), *fs)), i + 1
    if k == 'op' and v == '{':
        comps, i = parse_comps(L, i + 1); return ('struct', comps), i
    if k == 'arr':
        et = int(v[2:], 16); els = []; i += 1
        while True:
            while L[i][0] == 'nl': i += 1
            if L[i][:2] == ('op', ']'): break
            e, i = parse_value(L, i)
            if e[0] == 'raw' or TYPE_OF[e[0]] != et:
                if not (et == 2 and e[0] == 'int'): raise AsmError('line %d: array element does not match type %02x' % (L[i-1][2], et))
                e = ('float', struct.pack('<f', float(e[1])))
            els.append(e)
        return ('array', et, els, None), i + 1
    if k == 'raw':
        _, t, val = v.split(':'); return ('raw', int(t, 16), int(val, 16)), i + 1
    raise AsmError('line %d: unexpected %r in ARGS' % (ln, v))

def parse_comps(L, i):
    comps = []
    while True:
        while L[i][0] == 'nl': i += 1
        if L[i][:2] == ('op', '}'): return comps, i + 1
        name = 0
        if L[i][0] == 'name' and i + 1 < len(L) and L[i+1][:2] == ('op', '='):
            name = hashof(L[i][1]); i += 2
        val, i = parse_value(L, i)
        if val[0] == 'raw': comps.append((name, val[1], ('raw', val[2])))
        else: comps.append((name, TYPE_OF[val[0]] * 2 + 1, val))

def assemble(text):
    L = lex(text) + [('end', '', 0)]
    out = bytearray(); labels = {}; fix = []   # fix: (pos, size, label, base pos, line)
    stack = []; i = 0
    # "loose" scripts (IF/ELSE written with labels) are ones whose stored skips don't follow the usual nesting
    loose = any(L[j][0] == 'name' and L[j][1] in ('IF', 'ELSE') and L[j+1][0] == 'lref' for j in range(len(L) - 1))
    while L[i][0] != 'end':
        k, v, ln = L[i]; i += 1
        if k == 'nl': out.append(1)
        elif k == 'raws': out += bytes.fromhex(v[10:])
        elif k == 'rawt': out.append(int(v[1:3], 16)); out += bytes.fromhex(v[4:])
        elif k == 'rawa' or k == 'args':
            if k == 'rawa': blk = bytes.fromhex(v[5:])
            else:
                comps, i = parse_comps(L, i); e = Enc(); e.struct_(comps); blk = bytes(e.out)
            if len(blk) > 0xffff: raise AsmError('line %d: ARGS block too big' % ln)
            out += b'\x4a' + struct.pack('<H', len(blk)); out += b'\0' * ((-len(out)) % 4); out += blk
        elif k == 'ldef':
            if v[2:] in labels: raise AsmError('line %d: label %s defined twice' % (ln, v))
            labels[v[2:]] = len(out)
        elif k == 'line': out += b'\x02' + struct.pack('<I', int(v[6:]))
        elif k == 'str':
            pre, b = parse_str(v)
            out += {'': b'\x1b', 'L': b'\x1c', 'W': b'\x4c'}[pre] + struct.pack('<I', len(b)) + b
        elif k == 'pv':
            fs = [float(x) for x in v[v.index('(')+1:-1].split(',')]
            kind = v[:v.index('(')]
            if len(fs) != (2 if kind == 'pair' else 3): raise AsmError('line %d: bad %s' % (ln, kind))
            out += (b'\x1f' if kind == 'pair' else b'\x1e') + struct.pack('<%df' % len(fs), *fs)
        elif k == 'enum': out += b'\x19' + struct.pack('<i', int(v[5:]))
        elif k == 'hex': out += b'\x18' + struct.pack('<I', int(v, 16))
        elif k == 'num': out += num_tok(v)
        elif k == 'bare': out.append(int(v[1:], 16))
        elif k == 'arg': out += b'\x2d\x16' + struct.pack('<I', hashof(v[1:-1]))
        elif k == 'allargs': out.append(0x2c)
        elif k == 'rand':
            kw, body = v[:v.index('{')], v[v.index('{')+1:-1].split()
            ws = []; ls = []
            for part in body:
                w, l = part.split(':'); ws.append(int(w)); ls.append(l[1:])
            out.append(RANDB[kw]); out += struct.pack('<I', len(ws)) + struct.pack('<%dH' % len(ws), *ws)
            for l in ls: fix.append((len(out), 4, l, len(out) + 4, ln)); out += b'\0' * 4
        elif k == 'name' and v in ('IF', 'ELSE', 'SKIP', 'JUMP'):
            explicit = L[i][0] == 'lref'
            op = {'IF': 0x47, 'ELSE': 0x48, 'SKIP': 0x49, 'JUMP': 0x2e}[v]
            out.append(op)
            if v in ('SKIP', 'JUMP') and not explicit: raise AsmError('line %d: %s needs a label (@Ln)' % (ln, v))
            if op in (0x47, 0x48) and explicit != loose:
                raise AsmError('line %d: in one script either every IF/ELSE has a label or none has' % ln)
            if explicit:
                lab = L[i][1][1:]; i += 1
                if op == 0x2e: fix.append((len(out), 4, lab, len(out) + 4, ln)); out += b'\0' * 4
                else: fix.append((len(out), 2, lab, len(out), ln)); out += b'\0\0'
            else:
                if op == 0x48:
                    if not stack: raise AsmError('line %d: ELSE without IF' % ln)
                    u = stack.pop(); out += b'\0\0'
                    if u is not None: struct.pack_into('<H', out, u, len(out) - u)
                    stack.append(len(out) - 2)
                else: out += b'\0\0'; stack.append(len(out) - 2)
        elif k == 'name' and v in OPB: out.append(OPB[v])
        elif k == 'name': out += b'\x16' + struct.pack('<I', hashof(v))
        elif k == 'op':
            if v == '>=': raise AsmError('line %d: the >= token hangs this engine; use > or = instead' % ln)
            out.append(OPB[v])
        elif k == 'lref': raise AsmError('line %d: label %s must follow IF/ELSE/SKIP/JUMP' % (ln, v))
        else: raise AsmError('line %d: unexpected %r' % (ln, v))
        if k == 'name' and v == 'ENDIF' and not loose:
            if not stack: raise AsmError('line %d: ENDIF without IF' % ln)
            u = stack.pop()
            struct.pack_into('<H', out, u, len(out) - u)
    if stack: raise AsmError('IF without ENDIF (an IF near line %d is not closed)' % next(
        (ln for k, v, ln in L if k == 'name' and v == 'IF'), 0))
    for pos, size, lab, base, ln in fix:
        if lab not in labels: raise AsmError('line %d: label @%s is not defined' % (ln, lab))
        d = labels[lab] - base
        if size == 2:
            if not 0 <= d <= 0xffff: raise AsmError('line %d: jump to @%s out of range' % (ln, lab))
            struct.pack_into('<H', out, pos, d)
        else: struct.pack_into('<i', out, pos, d)
    return bytes(out)

# ---------------------------------------------------------------- containers
def item_list(d):
    return [(p, t, n) for p, t, n in items(d)]

def data_preview(d, p, t):
    """read-only text for a data item"""
    try:
        if t == 1: return 'int %d' % struct.unpack_from('<i', d, p + 12)[0]
        if t == 13: return 'checksum %s' % nm(struct.unpack_from('<I', d, p + 12)[0])
        if t == 3: return 'string ' + (fmt_str(cstr(d, struct.unpack_from('<I', d, p + 12)[0])) or '?')
        if t == 2: return 'float ' + (fmt_float(d[p+12:p+16]) or '?')
        if t == 10: return fmt_struct(dec_struct(d, struct.unpack_from('<I', d, p + 12)[0])[1])
        if t == 12: return fmt_val(dec_array(d, struct.unpack_from('<I', d, p + 12)[0], 0))
    except (Fail, struct.error, ValueError, IndexError, KeyError): pass
    return '(type %d, not decoded)' % t

def container_text(d, label=''):
    out = ['// qbrt source%s. Edit script bodies; data items are read-only. Build with tools\\qbrt.py.\n' % (' of ' + label if label else '')]
    for p, t, n in item_list(d):
        if t == 7:
            out.append('=== script %s\n%s\n\n' % (nm(n), disassemble(script_at(d, p))))
        else:
            out.append('=== data %s\n// %s\n\n' % (nm(n), data_preview(d, p, t).replace('\n', ' ')))
    return ''.join(out)

SECTION = re.compile(r'^=== (script|data) (\S+)[^\n]*\n', re.M)
def parse_sections(text):
    ms = list(SECTION.finditer(text)); res = []
    for k, m in enumerate(ms):
        body = text[m.end():ms[k+1].start() if k + 1 < len(ms) else len(text)]
        res.append((m.group(1), m.group(2), body))
    return res

def put_script(cont, p, plain):
    """write plain into the item's own slot if possible; returns new container or None"""
    hs, dsz, csz = struct.unpack_from('<3I', cont, p + 20)
    if len(plain) == csz:                       # store raw (csz == dsz means uncompressed to the engine)
        c = plain
    else:
        if len(compress(plain)) > csz: return None
        try: c = compress_exact(plain, csz)
        except ValueError: return None
    cont = bytearray(cont)
    struct.pack_into('<3I', cont, p + 20, hs, len(plain), csz); cont[p+32:p+32+csz] = c
    return bytes(cont)

def append_script(cont, p, name, plain):
    """rename the item at p to rt_old_<hash> and append a new one (header copied)"""
    cont = bytearray(cont); hdr = bytearray(cont[p:p+20]); hs = cont[p+20:p+24]
    struct.pack_into('<I', cont, p + 4, h('rt_old_%08x' % name))
    c = compress(plain)
    if len(c) >= len(plain): c = plain
    base = (len(cont) + 3) // 4 * 4; cont += b'\0' * (base - len(cont))
    struct.pack_into('<I', hdr, 12, base + 20)
    cont += hdr + hs + struct.pack('<2I', len(plain), len(c)) + c
    cont += b'\0' * ((-len(cont)) % 4); struct.pack_into('<I', cont, 4, len(cont))
    return bytes(cont)

def new_script(cont, name, plain):
    from qbedit import add_script
    return add_script(cont, name, plain)

def rebuild_container(d, text, log=print, label=''):
    """original container bytes + edited .qbs text -> new container bytes (d itself when nothing changed)"""
    # Items are matched by name and by occurrence (a few stock files define the same script name twice).
    # Original items never move: an edit is written into its own slot, or the item is renamed and a copy appended.
    its = item_list(d); slots = {}; kinds = {}
    for p, t, n in its:
        kinds.setdefault(n, t)
        if t == 7: slots.setdefault(n, []).append(p)
    cont = d; changed = []; used = {}
    for kind, name, body in parse_sections(text):
        if kind != 'script': continue
        n = hashof(name); k = used.get(n, 0); used[n] = k + 1
        try: plain = assemble(body)
        except AsmError as e: raise AsmError('%s, script %s: %s' % (label, name, e))
        if k < len(slots.get(n, [])):
            p = slots[n][k]
            if script_at(d, p) == plain: continue
            new = put_script(cont, p, plain)
            if new is None: cont = append_script(cont, p, n, plain); changed.append((name, 'moved to the end of the file'))
            else: cont = new; changed.append((name, 'in place'))
        elif n in kinds and kinds[n] != 7: raise AsmError('%s: %s is a data item, not a script' % (label, name))
        elif n in slots: raise AsmError('%s: script %s appears more often than in the original' % (label, name))
        else:
            cont = new_script(cont, n, plain); changed.append((name, 'new'))
    for n, ps in slots.items():
        if used.get(n, 0) < len(ps): log('  note: %s has no section for script %s; kept as stored' % (label, nm(n)))
    for name, how in changed: log('  %s: %s (%s)' % (label, name, how))
    return cont

# ---------------------------------------------------------------- qb.pak / qb.pab
ENT = 192
def pak_entries(pak, pab):
    """(entry pos, name, pab pos, size); entries can be 32 or 192 bytes (see qbpack.entries)"""
    import qbpack
    out = []
    for e, name, size, _k in qbpack.entries(pak):
        off = struct.unpack_from('<I', pak, e + 4)[0]
        out.append((e, name, off + e - len(pak), size))
    return out

def stable_pack(pak, pab, newfiles):
    """newfiles {name: bytes}; unchanged files keep their bytes and offsets, grown files move to the end of the pab"""
    pak = bytearray(pak); pab = bytearray(pab); ents = pak_entries(bytes(pak), bytes(pab)); grown = []
    if not any(newfiles.get(name, None) not in (None, pab[pos:pos+size]) for k, name, pos, size in ents):
        return bytes(pak), bytes(pab)
    for k, name, pos, size in ents:
        d = newfiles.get(name)
        if d is None or d == pab[pos:pos+size]: continue
        if len(d) <= size:
            pab[pos:pos+size] = d + b'\0' * (size - len(d)); struct.pack_into('<I', pak, k + 8, len(d))
        else: grown.append((k, name, d))
    if grown:
        end = max(pos + struct.unpack_from('<I', pak, k + 8)[0] for k, name, pos, size in ents)
        del pab[(end + 31) // 32 * 32:]
        for k, name, d in grown:
            pab += b'\0' * ((-len(pab)) % 32)
            struct.pack_into('<2I', pak, k + 4, len(pak) + len(pab) - k, len(d)); pab += d
            print('  %s grew; moved to the end of qb.pab' % name)
        pab += b'\0' * ((-len(pab)) % 32); pab += b'\xab' * ((-len(pab)) % 4096)
    return bytes(pak), bytes(pab)

def src_path(srcdir, name): return os.path.join(srcdir, name.replace('/', os.sep) + '.qbs') if not name.endswith('.qbs') else name

def cmd_decompile(pakp, pabp, srcdir):
    pak = open(pakp, 'rb').read(); pab = open(pabp, 'rb').read(); n = 0
    for k, name, pos, size in pak_entries(pak, pab):
        if not name.endswith('.qb.wpc'): continue
        p = src_path(srcdir, name); os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'w', encoding='utf8', newline='\n').write(container_text(pab[pos:pos+size], name)); n += 1
    open(os.path.join(srcdir, '_qbrt_base.txt'), 'w').write('decompiled from\n%s  sha1 %s\n%s  sha1 %s\nbuild against these same files.\n' % (
        os.path.abspath(pakp), hashlib.sha1(pak).hexdigest(), os.path.abspath(pabp), hashlib.sha1(pab).hexdigest()))
    print('wrote', n, 'files to', srcdir)

def cmd_build(pakp, pabp, srcdir, outpak, outpab):
    pak = open(pakp, 'rb').read(); pab = open(pabp, 'rb').read(); new = {}
    for k, name, pos, size in pak_entries(pak, pab):
        p = src_path(srcdir, name)
        if not name.endswith('.qb.wpc') or not os.path.exists(p): continue
        d = pab[pos:pos+size]
        nd = rebuild_container(d, open(p, encoding='utf8').read(), label=name)
        if nd != d: new[name] = nd
    opak, opab = stable_pack(pak, pab, new)
    open(outpak, 'wb').write(opak); open(outpab, 'wb').write(opab)
    print('changed files:', len(new), '| pak identical:', opak == pak, '| pab identical:', opab == pab)

def cmd_zdecompile(zp, srcdir):
    d = open(zp, 'rb').read(); n = 0
    for e in zonepak.entries(d):
        blob = d[e['abs']:e['abs']+e['size']]
        if not zonepak.is_qb(blob): continue
        fn = (e['fname'] or '%04d_%08x' % (e['i'], e['name'])).replace('\\', '/')
        p = src_path(srcdir, fn); os.makedirs(os.path.dirname(p) or '.', exist_ok=True)
        open(p, 'w', encoding='utf8', newline='\n').write(container_text(blob, '%s (zone entry %d)' % (fn, e['i']))); n += 1
    print('wrote', n, 'files to', srcdir)

def cmd_zbuild(zp, srcdir, outp):
    d = open(zp, 'rb').read(); orig = d
    for e in zonepak.entries(orig):
        blob = orig[e['abs']:e['abs']+e['size']]
        if not zonepak.is_qb(blob): continue
        fn = (e['fname'] or '%04d_%08x' % (e['i'], e['name'])).replace('\\', '/')
        p = src_path(srcdir, fn)
        if not os.path.exists(p): continue
        nb = rebuild_container(blob, open(p, encoding='utf8').read(), label=fn)
        if nb != blob: d = zonepak.replace_file(d, e['i'], nb)
    open(outp, 'wb').write(d); print('zone pak identical:', d == orig)

def cmd_verify(pakp, pabp):
    pak = open(pakp, 'rb').read(); pab = open(pabp, 'rb').read()
    tot = ok = rawscript = rawargs = args = rawtok = 0; bad = []; texts = {}
    for k, name, pos, size in pak_entries(pak, pab):
        if not name.endswith('.qb.wpc'): continue
        d = pab[pos:pos+size]; t = container_text(d, name); texts[name] = t
        scripts = [p for p, ty, n in item_list(d) if ty == 7]
        for (kind, sname, body), p in zip([x for x in parse_sections(t) if x[0] == 'script'], scripts):
            tot += 1
            s = script_at(d, p)
            try: good = assemble(body) == s
            except AsmError as e: good = False
            ok += good
            if not good and len(bad) < 10: bad.append((name, sname))
            rawscript += body.startswith('RAWSCRIPT#'); rawargs += body.count('ARGS#'); args += body.count('ARGS{')
            rawtok += len(re.findall(r'(?<![A-Za-z])#[0-9a-f]{2}:', body))
    print('scripts', tot, 'exact round trip', ok, 'failed', tot - ok, bad)
    print('readable ARGS blocks', args, 'raw ARGS blocks', rawargs, 'raw scripts', rawscript, 'raw tokens', rawtok)
    new = {}
    for k, name, pos, size in pak_entries(pak, pab):
        if name in texts:
            d = pab[pos:pos+size]; nd = rebuild_container(d, texts[name], label=name, log=lambda *a: None)
            if nd != d: new[name] = nd
    opak, opab = stable_pack(pak, pab, new)
    print('unedited rebuild: changed files', len(new), 'pak identical', opak == pak, 'pab identical', opab == pab)

if __name__ == '__main__':
    a = sys.argv[1:]
    try:
        if a[:1] == ['decompile']: cmd_decompile(*a[1:4])
        elif a[:1] == ['build']: cmd_build(*a[1:6])
        elif a[:1] == ['dis']:
            open(a[2], 'w', encoding='utf8', newline='\n').write(container_text(open(a[1], 'rb').read(), os.path.basename(a[1])))
        elif a[:1] == ['asm']:
            d = open(a[1], 'rb').read(); nd = rebuild_container(d, open(a[2], encoding='utf8').read(), label=os.path.basename(a[1]))
            open(a[3], 'wb').write(nd); print('identical to the original:', nd == d)
        elif a[:1] == ['zdecompile']: cmd_zdecompile(*a[1:3])
        elif a[:1] == ['zbuild']: cmd_zbuild(*a[1:4])
        elif a[:1] == ['verify']: cmd_verify(*a[1:3])
        else: print(__doc__)
    except AsmError as e:
        print('ERROR:', e); sys.exit(1)
