"""LZSS compressor (optimal parse) compatible with the ReTHAWed/THAW QB script decoder (ring 4096, start 4078, len 3..18)."""
N, F, THR, R0 = 4096, 18, 3, 4078

def compress(data):
    n = len(data)
    # longest match (any distance <= N-F) for each position
    chains = {}
    mlen = [0] * n; mpos = [0] * n
    for i in range(n):
        if i + 3 <= n:
            key = data[i:i+3]
            lst = chains.get(key)
            if lst:
                best = 0; bp = 0
                for s in reversed(lst[-512:]):
                    if i - s > N - F: break
                    l = 3
                    while l < F and i + l < n and data[s + l] == data[i + l]: l += 1
                    if l > best:
                        best = l; bp = s
                        if l == F: break
                mlen[i] = best; mpos[i] = bp
            chains.setdefault(key, []).append(i)
    INF = 1 << 60
    cost = [INF] * (n + 1); step = [0] * (n + 1); cost[n] = 0
    for i in range(n - 1, -1, -1):
        c = 9 + cost[i + 1]; st = 1
        for L in range(THR, mlen[i] + 1):
            cc = 17 + cost[i + L]
            if cc < c: c = cc; st = L
        cost[i] = c; step[i] = st
    out = bytearray(); flags = 0; nbits = 0; buf = bytearray(); i = 0
    while i < n:
        L = step[i]
        if L == 1:
            flags |= 1 << nbits; buf.append(data[i])
        else:
            ring = (R0 + mpos[i]) % N
            buf += bytes([ring & 0xff, ((ring >> 4) & 0xf0) | (L - THR)])
        nbits += 1; i += L
        if nbits == 8:
            out.append(flags); out += buf; flags = 0; nbits = 0; buf = bytearray()
    if nbits: out.append(flags); out += buf
    return bytes(out)


def _matches(data):
    n = len(data); chains = {}
    mlen = [0] * n; mpos = [0] * n
    for i in range(n):
        if i + 3 <= n:
            key = data[i:i+3]; lst = chains.get(key)
            if lst:
                best = 0; bp = 0
                for s in reversed(lst[-512:]):
                    if i - s > N - F: break
                    l = 3
                    while l < F and i + l < n and data[s + l] == data[i + l]: l += 1
                    if l > best:
                        best = l; bp = s
                        if l == F: break
                mlen[i] = best; mpos[i] = bp
            chains.setdefault(key, []).append(i)
    return mlen, mpos

def compress_exact(data, target):
    """LZSS stream of EXACTLY `target` bytes that decodes to `data` (the engine appears to consume the whole stored size)."""
    n = len(data); mlen, mpos = _matches(data)
    reach = [[0] * 8 for _ in range(n + 1)]
    for k in range(8): reach[n][k] = 1
    for i in range(n - 1, -1, -1):
        for k in range(8):
            k2 = (k + 1) % 8; fl = 1 if k == 0 else 0
            r = reach[i + 1][k2] << (1 + fl)
            for L in range(3, mlen[i] + 1):
                r |= reach[i + L][k2] << (2 + fl)
            reach[i][k] = r
    if not (reach[0][0] >> target) & 1:
        raise ValueError('cannot hit exact size %d' % target)
    out = bytearray(); flags = 0; nbits = 0; buf = bytearray(); i = 0; k = 0; rem = target
    while i < n:
        k2 = (k + 1) % 8; fl = 1 if k == 0 else 0
        step = None
        for L in range(min(mlen[i], F), 2, -1):           # prefer matches, longest first
            if (reach[i + L][k2] >> (rem - 2 - fl)) & 1 and rem - 2 - fl >= 0: step = L; break
        if step is None:
            assert (reach[i + 1][k2] >> (rem - 1 - fl)) & 1; step = 1
        if step == 1:
            flags |= 1 << nbits; buf.append(data[i]); rem -= 1 + fl
        else:
            ring = (R0 + mpos[i]) % N
            buf += bytes([ring & 0xff, ((ring >> 4) & 0xf0) | (step - THR)]); rem -= 2 + fl
        nbits += 1; i += step; k = k2
        if nbits == 8:
            out.append(flags); out += buf; flags = 0; nbits = 0; buf = bytearray()
    if nbits: out.append(flags); out += buf
    assert len(out) == target and rem == 0, (len(out), target, rem)
    return bytes(out)
