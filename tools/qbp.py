import struct,sys,zlib,re
sys.path.insert(0,'/tmp/claude-0/-home-claude/ef624d97-8dab-519d-92ef-3f4cbb5b0699/scratchpad')
from lz import lzss
h=lambda s:(~zlib.crc32(s.lower().encode('latin1')))&0xffffffff
def items(d):
    fcrc=d[0x24:0x28]
    res=[]
    for m in re.finditer(re.escape(fcrc),d):
        p=m.start()-8
        if p>=0x1c and d[p]==0 and d[p+1]==4 and d[p+3]==0:
            res.append((p,d[p+2],struct.unpack('<I',d[p+4:p+8])[0]))
    return res
def script_at(d,p):
    hs,dsz,csz=struct.unpack('<3I',d[p+20:p+32])
    if csz==dsz: return d[p+32:p+32+csz]   # stored uncompressed (e.g. classic_mode_menu $d5ccb8af)
    return lzss(d[p+32:p+32+csz],dsz,4078,0x20)   # ring pre-filled with spaces (0x20 = BEGIN token); 0 broke 429 scripts
