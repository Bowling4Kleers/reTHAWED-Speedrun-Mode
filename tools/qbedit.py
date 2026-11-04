"""Replace a compressed script item inside a QB container, in place (no offsets shift).
The new compressed stream must not be larger than the original slot."""
import sys, os, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qbcomp import compress, compress_exact
try: from qbp import items
except ImportError: items = None

R0 = 4078
def replace_script(cont, name_hash, new_plain, items_fn=None):
    items_fn = items_fn or items
    cont = bytearray(cont)
    for p, t, name in items_fn(bytes(cont)):
        if t == 7 and name == name_hash:
            hs, dsz, csz = struct.unpack('<3I', cont[p+20:p+32])
            c = compress(new_plain)
            if len(c) > csz:
                raise ValueError('compressed script is %d bytes, slot holds %d' % (len(c), csz))
            # keep csz = slot size: the engine walks items by this field, so shrinking it would desync the container
            cont[p+20:p+32] = struct.pack('<3I', hs, len(new_plain), csz)
            c = compress_exact(new_plain, csz)    # stream must be exactly csz bytes (no zero padding)
            cont[p+32:p+32+csz] = c
            return bytes(cont)
    raise KeyError(hex(name_hash))

def add_script(cont, name_hash, plain, items_fn=None):
    """Append a brand-new script item (header copied from the container's first script item)."""
    items_fn = items_fn or items
    cont = bytearray(cont)
    for p, t, name in items_fn(bytes(cont)):
        assert name != name_hash, 'script %08x already exists' % name_hash
    p = next(p for p, t, name in items_fn(bytes(cont)) if t == 7)
    hdr = bytearray(cont[p:p+20]); hs = cont[p+20:p+24]
    hdr[4:8] = struct.pack('<I', name_hash)
    c = compress(plain)
    if len(c) >= len(plain): c = plain   # stored raw (csz == dsz), as the game does; v27 crashed on csz > dsz
    base = (len(cont) + 3) // 4 * 4
    cont += b'\0' * (base - len(cont))
    hdr[12:16] = struct.pack('<I', base + 20)
    cont += hdr + hs + struct.pack('<2I', len(plain), len(c)) + c
    cont += b'\0' * ((-len(cont)) % 4)
    cont[4:8] = struct.pack('<I', len(cont))
    return bytes(cont)

def add_global(cont, name_hash, kind, value, items_fn=None):
    """Append a global symbol item. A global must exist as an item: `change` on an undefined name does nothing.
    kind: 'int' (type 1, value in the header), 'checksum' (type 0x0d, value in the header), 'string' (type 3, NUL string after the header)."""
    items_fn = items_fn or items
    cont = bytearray(cont)
    its = items_fn(bytes(cont))
    assert all(n != name_hash for p, t, n in its), 'global %08x already exists' % name_hash
    fh = cont[its[0][0]+8:its[0][0]+12]
    base = (len(cont) + 3) // 4 * 4
    cont += b'\0' * (base - len(cont))
    if kind == 'string':
        s = value.encode('latin1') + b'\0'; s += b'\0' * ((-len(s)) % 4)
        cont += bytes([0, 4, 3, 0]) + struct.pack('<I', name_hash) + fh + struct.pack('<2I', base + 20, 0) + s
    else:
        t = {'int': 1, 'checksum': 0x0d}[kind]
        cont += bytes([0, 4, t, 0]) + struct.pack('<I', name_hash) + fh + struct.pack('<iI', value, 0)
    cont[4:8] = struct.pack('<I', len(cont))
    return bytes(cont)

def relocate_script(cont, name_hash, new_plain, old_name, items_fn=None):
    """For scripts that outgrow their slot: rename the original item to old_name (left in place, never called)
    and append a new script item with name_hash at the end of the container (same header layout as the originals)."""
    items_fn = items_fn or items
    cont = bytearray(cont)
    for p, t, name in items_fn(bytes(cont)):
        if t == 7 and name == name_hash:
            hdr = bytearray(cont[p:p+20]); hs = cont[p+20:p+24]
            cont[p+4:p+8] = struct.pack('<I', old_name)
            break
    else:
        raise KeyError(hex(name_hash))
    c = compress(new_plain)
    if len(c) >= len(new_plain) or os.environ.get('RELOC_RAW'): c = new_plain
    base = (len(cont) + 3) // 4 * 4
    cont += b'\0' * (base - len(cont))
    hdr[12:16] = struct.pack('<I', base + 20)
    cont += hdr + hs + struct.pack('<2I', len(new_plain), len(c)) + c
    cont += b'\0' * ((-len(cont)) % 4)
    cont[4:8] = struct.pack('<I', len(cont))
    return bytes(cont)
