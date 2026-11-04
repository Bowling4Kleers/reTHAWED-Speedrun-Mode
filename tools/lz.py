import struct
def lzss(src, outsize, r0, init=0, N=4096, thr=3, hi=True):
    buf=bytearray([init])*N; r=r0; out=bytearray(); i=0
    while len(out)<outsize and i<len(src):
        fl=src[i]; i+=1
        for b in range(8):
            if len(out)>=outsize or i>=len(src): break
            if fl>>b&1:
                c=src[i]; i+=1; out.append(c); buf[r]=c; r=(r+1)%N
            else:
                if i+1>=len(src)+0 and i+1>len(src)-1+0: pass
                b0=src[i]; b1=src[i+1]; i+=2
                pos=b0|((b1&0xf0)<<4); ln=(b1&0x0f)+thr
                for k in range(ln):
                    c=buf[(pos+k)%N]; out.append(c); buf[r]=c; r=(r+1)%N
                    if len(out)>=outsize: break
    return bytes(out)
