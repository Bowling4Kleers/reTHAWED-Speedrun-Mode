import struct,re,zlib,os,sys,collections
FIXED={0x16:4,0x17:4,0x1a:4,0x1d:8,0x1e:12,0x02:4,0x2b:0}
def tokenize(s, strict=True):
    i=0; out=[]
    while i<len(s):
        t=s[i]; st=i; i+=1
        if t in (0x1b,0x1c):
            n=struct.unpack('<I',s[i:i+4])[0]; i+=4
            if t==0x1c: n*=1  # wide: assume bytes length
            out.append((st,t,s[i:i+n])); i+=n
        elif t in FIXED:
            n=FIXED[t]; out.append((st,t,s[i:i+n])); i+=n
        else:
            out.append((st,t,b''))
    return out
