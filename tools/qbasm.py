"""Tiny QB script assembler (THAW/ReTHAWed token format), enough for hand-written mod scripts.

A script is a list of items:
  'name' / 0x1234abcd      name token (16 + hash); a str starting with '$' is a hex hash
  int / float              17 / 1a
  S('text')                string 1b (NUL added)
  P(x, y)                  pair 1f
  '=' '(' ')' '+' '-' '/' '*' '<' '>' '{' '}' '[' ']' ',' '.'    operators
  '<'name                  '<ARG>' then the name ('<x' -> 2d 16 hash(x))
  NL, NOT, BEGIN, REPEAT, BREAK, ENDSCRIPT, ENDIF
  IF, ELSE                 skip offsets are resolved by asm()
IF/ELSE skip = (position of the skip u16) + skip -> first byte after the matching ELSE header / ENDIF.
"""
import struct, zlib

def h(s): return (~zlib.crc32(s.lower().encode('latin1'))) & 0xffffffff

class S(str): pass
class P(tuple):
    def __new__(cls, x, y): return tuple.__new__(cls, (x, y))
class _Op:
    def __init__(self, b, name): self.b = b; self.name = name
    def __repr__(self): return self.name
NL = _Op(b'\x01', 'NL'); NOT = _Op(b'\x39', 'NOT'); BEGIN = _Op(b'\x20', 'BEGIN'); REPEAT = _Op(b'\x21', 'REPEAT')
BREAK = _Op(b'\x22', 'BREAK'); ENDSCRIPT = _Op(b'\x24', 'ENDSCRIPT'); ENDIF = _Op(b'\x28', 'ENDIF')
IF = _Op(b'\x47', 'IF'); ELSE = _Op(b'\x48', 'ELSE'); RETURN = _Op(b'\x29', 'RETURN')
PASS = _Op(b'\x2c', '<...>')   # pass all of the caller's params
OPS = {'=': 7, '(': 0x0e, ')': 0x0f, '+': 0x0b, '-': 0x0a, '/': 0x0c, '*': 0x0d, '<': 0x12, '<=': 0x13, '>': 0x14, '>=': 0x15,
       '{': 3, '}': 4, '[': 5, ']': 6, ',': 9, '.': 8}

def name_hash(x):
    if isinstance(x, int): return x
    return int(x[1:], 16) if x.startswith('$') else h(x)

def asm(prog):
    out = bytearray(); stack = []   # stack of [kind, u16 position]
    for t in prog:
        if t is IF:
            out += b'\x47\0\0'; stack.append(['if', len(out) - 2])
        elif t is ELSE:
            k, pos = stack.pop(); assert k == 'if'
            out += b'\x48\0\0'
            out[pos:pos+2] = struct.pack('<H', len(out) - pos)
            stack.append(['else', len(out) - 2])
        elif t is ENDIF:
            k, pos = stack.pop()
            out += b'\x28'
            out[pos:pos+2] = struct.pack('<H', len(out) - pos)
        elif isinstance(t, _Op): out += t.b
        elif isinstance(t, S):
            b = t.encode('latin1') + b'\0'; out += b'\x1b' + struct.pack('<I', len(b)) + b
        elif isinstance(t, P): out += b'\x1f' + struct.pack('<2f', *t)
        elif isinstance(t, Hash): out += b'\x16' + struct.pack('<I', int(t))
        elif isinstance(t, bool): raise TypeError(t)
        elif isinstance(t, int): out += b'\x17' + struct.pack('<i', t)
        elif isinstance(t, float): out += b'\x1a' + struct.pack('<f', t)
        elif isinstance(t, str) and t in OPS: out += bytes([OPS[t]])
        elif isinstance(t, str) and t.startswith('<') and len(t) > 1: out += b'\x2d\x16' + struct.pack('<I', name_hash(t[1:]))
        elif isinstance(t, str): out += b'\x16' + struct.pack('<I', name_hash(t))
        else: raise TypeError(repr(t))
    assert not stack, 'unclosed IF'
    return bytes(out)

class Hash(int):
    """A raw hash value used as a name token (so it is not encoded as an integer)."""
