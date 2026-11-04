#!/usr/bin/env python3
"""Build v1 of the 'Classic Speedrun' experiment from pristine qb.pak/pab.
  python build_speedrun_v1.py <orig qb.pak.wpc> <orig qb.pab.wpc> <workdir> <out dir>
Changes:
  1. gamemodes/gamemode.qb : new mode def (Free Skate settings + Classic clock=120s) and a 34-entry game-mode array
     (old array renamed so there is no duplicate item name).
  2. game/startup.qb       : ini 'AutolaunchGame=speedrun' now selects the new mode (replaces the unused 'graffiti' case).
  3. GOAL_TIME=<secs>      : the Classic TimedScoreRun goal gets that time limit, only under GameModeEquals rt_speedrun.
                             HIDE_TIMER=1 also adds the unlimited_time goal flag. GOAL_TIME_MODE=relocate keeps view_goals_text.
  4. COUNTUP_CLOCK=1       : new script rt_speedrun_clock (count-up HUD), spawned by the TimedScoreRun activate script.
  PACK=stable              : keep every pab file at its original offset (required whenever a file grows; see HANDOFF).
"""
import sys, os, struct, zlib, shutil, subprocess
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
from qbp import items, script_at
NORM_NL = bool(os.environ.get('NORM_NL'))   # reTHAWed 5.0.1.0+: the new compiler writes every newline token twice (01 01).
if NORM_NL:   # read scripts with each run of 2n newline tokens collapsed to n, so the builder's token patterns match the old
    import re as _re, qbrt as _qbrt    # compiler's output; the engine treats extra newline tokens as no-ops
    _raw_script_at = script_at
    def script_at(d, p):
        s = _raw_script_at(d, p)
        if b'\x01\x01' not in s: return s
        t = _qbrt.disassemble(s)
        if t.startswith('RAWSCRIPT'): return s
        return _qbrt.assemble(_re.sub(r'\n+', lambda m: '\n' * max(1, len(m.group()) // 2), t))
from qbedit import replace_script, relocate_script, add_script, add_global
h = lambda s: (~zlib.crc32(s.lower().encode('latin1'))) & 0xffffffff
u32 = lambda d, p: struct.unpack('<I', d[p:p+4])[0]
P = lambda *a: struct.pack('<%dI' % len(a), *a)
def al4(n): return (n + 3) // 4 * 4

MODE_ID   = h('classic_speedrun')
DEF_NAME  = h('rt_classic_speedrun_def')
OLD_ARRAY = h('rt_old_gamemode_array')
ARRAY     = 0xca5f7ffb
FREESKATE_DEF = 0x535837ae
CLASSIC_DEF = 0x9d8649d0
TEMPLATE_DEF = CLASSIC_DEF if os.environ.get('SPEEDRUN_TEMPLATE', 'classic') == 'classic' else FREESKATE_DEF
# optional overrides on the cloned definition, e.g. SPEEDRUN_FLAGS='8d38a280=120,c1f5864b=1'
def _hk(k):
    try: return int(k, 16)
    except ValueError: return (~zlib.crc32(k.lower().encode())) & 0xffffffff
OVERRIDES = {_hk(k): int(v) for k, v in (x.split('=') for x in os.environ.get('SPEEDRUN_FLAGS', '').split(',') if x)}
CLOCK_FLAG = 0x8d38a280

def patch_gamemode(d):
    d = bytearray(d); fcrc = u32(d, 0x24)
    tbl = {n: p for p, t, n in items(bytes(d))}
    # --- clone Free Skate definition, with the classic clock set to 120s
    tp = tbl[TEMPLATE_DEF]; fp = tbl[FREESKATE_DEF]; fdata = u32(d, tp + 12); first = u32(d, fdata + 4)
    comps = []; q = first
    while q:
        fl, name, val, nxt = struct.unpack('<4I', d[q:q+16]); comps.append([fl, name, val]); q = nxt
    for c in comps:
        if c[1] in OVERRIDES: c[2] = OVERRIDES[c[1]]
    intflag = next(c[0] for c in comps if c[0] == 0x300 or (c[0] >> 8) == 3)
    for k, v in OVERRIDES.items():                       # flags the template doesn't have are appended as new integer components
        if not any(c[1] == k for c in comps): comps.append([intflag, k, v])
    base = al4(len(d)); out = bytearray()
    out += P(0x000a0400 + 0, DEF_NAME, fcrc, base + 20, 0)          # item header: 00 04 0a 00
    out += P(0x00010000, base + 28)                                   # struct header
    for i, (fl, name, val) in enumerate(comps):
        nxt = base + 28 + 16 * (i + 1) if i + 1 < len(comps) else 0
        out += P(fl, name, val, nxt)
    # --- new 34 entry array
    arr_p = tbl[ARRAY]; adata = u32(d, arr_p + 12)
    aflags, cnt, tab = struct.unpack('<3I', d[adata:adata+12])
    offs = list(struct.unpack('<%dI' % cnt, d[tab:tab+4*cnt]))
    # element template (first existing element) for component flags + key names
    e0 = offs[0]; ef, efirst = struct.unpack('<2I', d[e0:e0+8])
    tmpl = []; q = efirst
    while q:
        fl, name, val, nxt = struct.unpack('<4I', d[q:q+16]); tmpl.append([fl, name, val]); q = nxt
    assert len(tmpl) == 4, len(tmpl)
    a0 = base + len(out); a0 += (-a0) % 4
    out += b'\0' * (a0 - base - len(out))
    n = cnt + 1
    tab_new = a0 + 32
    # self-contained array like the originals: table, then every element copied in order, then the new one.
    ends = offs[1:] + [fp]                    # last original element ends where the freeskate def item starts
    els = []; pos = tab_new + 4 * n
    for o, e in zip(offs, ends):
        els.append((o, e, pos)); pos += e - o
    title = b'Classic Speedrun\0'; title += b'\0' * (al4(len(title)) - len(title))
    elem = pos
    out += P(0x000c0400 + 0, ARRAY, fcrc, a0 + 20, 0)
    out += P(aflags, n, tab_new)
    out += struct.pack('<%dI' % n, *([x[2] for x in els] + [elem]))
    for o, e, np_ in els:
        delta = np_ - o; blk = bytearray(d[o:e])
        f0, first = struct.unpack('<2I', blk[:8]); blk[4:8] = struct.pack('<I', first + delta)
        q = first
        while q:
            r = q - o
            fl_, nm, val, nx = struct.unpack('<4I', blk[r:r+16])
            if (fl_ >> 8) & 0xff == 7: val += delta
            if nx: blk[r+12:r+16] = struct.pack('<I', nx + delta)
            blk[r+8:r+12] = struct.pack('<I', val)
            q = nx
        out += blk
    # new element: [struct hdr][NAME comp][string][3 more comps], same layout as the originals
    c0 = elem + 8; sp = c0 + 16; c1 = sp + len(title)
    out += P(0x00010000, c0)
    for i, (fl, name, val) in enumerate(tmpl):
        if i == 0: out += P(fl, name, sp, c1)
        else:
            v = MODE_ID if i == 1 else (DEF_NAME if i == 2 else val)
            at = c1 + 16 * (i - 1)
            out += P(fl, name, v, at + 16 if i + 1 < len(tmpl) else 0)
        if i == 0: out += title
    d += b'\0' * (base - len(d))
    d += out
    d[4:8] = struct.pack('<I', len(d))
    d[arr_p+4:arr_p+8] = struct.pack('<I', OLD_ARRAY)    # old array: unique unused name
    return bytes(d)

def patch_startup(d):
    old = b'graffiti\x00'; new = b'speedrun\x00'
    gh = struct.pack('<I', h('graffiti')); nh = struct.pack('<I', MODE_ID)
    for p, t, name in items(d):
        if t != 7: continue
        s = script_at(d, p)
        if b'Unknown autolaunch' in s and old in s:
            i = s.index(old); assert s[i-5] == 0x1b
            j = s.index(b'\x16' + gh, i)
            s2 = s[:i] + new + s[i+len(old):j+1] + nh + s[j+5:]
            assert len(s2) == len(s)
            return replace_script(d, name, s2)
    raise SystemExit('autolaunch switch not found')

def patch_levelselect(d):
    # In level select, 'unlock everything' is `IntegerEquals $174841bc = $e9c2cd10 $8e411006 = 1`. Make it `GameModeEquals <flag>` (same length, newline padding).
    flag = _hk(os.environ['UNLOCK_FLAG'])
    old = bytes.fromhex('162a197e9316bc414817071610cdc2e9160610418e0717' + '01000000')
    new = b'\x16' + struct.pack('<I', 0x20985922) + b'\x16' + struct.pack('<I', flag)
    if SR_GLOBAL:       # ( rt_speedrun_on = 1 )
        new = b'\x0e\x16' + struct.pack('<I', h('rt_speedrun_on')) + b'\x07\x17' + struct.pack('<i', 1) + b'\x0f'
    new += b'\x01' * (len(old) - len(new))
    for p, t, name in items(d):
        if t == 7:
            sc = script_at(d, p)
            if sc.count(old) == 1:
                return replace_script(d, name, sc.replace(old, new))
    raise SystemExit('level-select unlock check not found')

SR_GLOBAL = bool(os.environ.get('SR_GLOBAL'))
SR_LOG = bool(os.environ.get('SR_LOG', '1'))           # Printf 'RTSR ...' markers in debug.txt
AUTOWIN = bool(os.environ.get('AUTOWIN'))              # TEST ONLY: wins every goal of the level, a few seconds apart
SR_DEFAULT_ON = bool(os.environ.get('SR_DEFAULT_ON'))  # TEST ONLY: rt_speedrun_on starts at 1 (ini autolaunch skips the menu script)

def a4(b):
    # inline-argument blocks (0x4A) are padded to 4-byte boundaries counted from the script start, so every insertion
    # must be a multiple of 4 bytes (v20 crashed at startup after a 6-byte insert in 6d344afd). Pad with newlines.
    return b + b'\x01' * ((-len(b)) % 4)
# SR_GLOBAL (v20+): speedrun is the real Classic mode (same mode id, so the ~37 scripts that check for Classic's id behave as in Classic,
# e.g. the TimedScoreRun expire -> end-of-run) plus the global rt_speedrun_on = 1, set by the Classic menu script 6d344afd.

def sr_wrap(body):
    """asm tokens: run body only in speedrun"""
    from qbasm import NL, IF, ENDIF
    if SR_GLOBAL:
        return [IF, '(', 'rt_speedrun_on', '=', 1, ')', NL, IF, 'GameModeEquals', '$5a2c589a', NL] + body + [ENDIF, NL, ENDIF, NL]
    return [IF, 'GameModeEquals', 'rt_speedrun', NL] + body + [ENDIF, NL]

def patch_goaltime(d):
    # Classic's 2:00 run is one TimedScoreRun goal added at level load by script $d0b3442c (AddGoal ... time = 120).
    # Append: IF <speedrun>  GoalManager_EditGoal NAME = <goal_id> Params = { time = GOAL_TIME }  ENDIF
    if SR_GLOBAL:
        from qbasm import asm, NL
        p = next(p for p, t, n in items(d) if t == 7 and n == 0xd0b3442c)
        s = script_at(d, p); assert s.endswith(b'\x01\x04\x01\x04\x01\x24')
        flags = ['unlimited_time'] if os.environ.get('HIDE_TIMER') else []
        add = asm(sr_wrap(['GoalManager_EditGoal', 'name', '=', '<goal_id', 'params', '=', '{', 'time', '=', int(os.environ['GOAL_TIME'])] + flags + ['}', NL]))
        if CLASSIC_TIMER:
            add = a4(add) + a4(classic_timer_block())
            d = add_script(d, h('rt_classic_time_get'), classic_time_get_script())
        return relocate_script(d, 0xd0b3442c, s[:-1] + a4(add) + b'\x24', h('rt_old_timedscorerun_add'))
    N = lambda x: b'\x16' + struct.pack('<I', x)
    secs = int(os.environ['GOAL_TIME'])
    for p, t, name in items(d):
        if t == 7 and name == 0xd0b3442c:
            s = script_at(d, p); break
    else:
        raise SystemExit('TimedScoreRun add-goal script not found')
    assert s.endswith(b'\x01\x04\x01\x04\x01\x24')
    body = (b'\x01' + N(0x9c2d793e) + N(0xa1dc81f9) + b'\x07\x2d' + N(0x9982e501)          # GoalManager_EditGoal NAME = <goal_id>
            + N(0x7031f10c) + b'\x07\x03' + N(0x906b67ba) + b'\x07\x17' + struct.pack('<i', secs)
            + (N(h('unlimited_time')) if os.environ.get('HIDE_TIMER') else b'')                 # flag: no native countdown / expiry
            + b'\x04\x01\x28')
    cond = N(0x20985922) + N(_hk(os.environ.get('UNLOCK_FLAG', 'rt_speedrun')))            # GameModeEquals <flag>
    blk = b'\x47' + struct.pack('<H', 2 + len(cond) + len(body)) + cond + body + b'\x01'  # IF skip = from the u16 to past ENDIF
    s = s[:-1] + blk + b'\x24'
    if os.environ.get('GOAL_TIME_MODE') == 'relocate':      # v13: crashed at startup with a qb.pab FILE ERROR
        return relocate_script(d, 0xd0b3442c, s, h('rt_old_timedscorerun_add'))
    # in place: make room by dropping the generic view_goals_text param (Classic shows its own goal list; readers use GotParam)
    old = b'\x01\x16\x9d\x22\xc5\x4b\x07\x1b\x1d\x00\x00\x00Generic timed score run text\x00'
    assert s.count(old) == 1
    s = s.replace(old, b'')
    return replace_script(d, 0xd0b3442c, s)

CLASSIC_TIMER = bool(os.environ.get('CLASSIC_TIMER'))   # plain Classic: run length from rethawed.ini [ClassicMode] RunTimer (seconds) + a General Options row
CT_SECTION = 'ClassicMode'; CT_KEY = 'RunTimer'
CT_PRESETS = (120, 300, 600, 1200, 3599)
CLASSIC_HIDE = bool(os.environ.get('CLASSIC_HIDE'))     # v47: [ClassicMode] HideTimer=1 hides Classic's on-screen goal timer (the countdown still runs)
CLASSIC_HC_LOCK = bool(os.environ.get('CLASSIC_HC_LOCK'))   # v47: a hardcore Classic tour always gets the stock 2:00, and the row is locked in a run
CT_HIDE_KEY = 'HideTimer'

def classic_timer_block():
    """asm: appended to $d0b3442c at top level (its own block, speedrun's IF above is untouched). Classic only, never speedrun."""
    from qbasm import asm, S, NL, IF, ELSE, ENDIF
    return asm([IF, '(', 'rt_speedrun_on', '=', 0, ')', NL, IF, 'GameModeEquals', '$5a2c589a', NL,
        'rt_classic_time_get', NL] + (
        # hardcore tours keep the stock clock ($b21bafd2 = classic_mode_menu's "tour difficulty is HARDCORE", as ReThawed's own preset code uses it)
        [IF, '$b21bafd2', NL, 'rt_ct', '=', 120, NL, 'Printf', S('RTCT hardcore tour: stock 2:00'), NL, ENDIF, NL] if CLASSIC_HC_LOCK else []) + [
        IF, '(', '<rt_ct', '=', 120, ')', NL, ELSE, NL,     # 2:00 is the stock value: leave the goal alone
            'GoalManager_EditGoal', 'name', '=', '<goal_id', 'params', '=', '{', 'time', '=', '<rt_ct', '}', NL, ENDIF, NL,
        'Printf', S('RTCT run timer %a s'), 'a', '=', '<rt_ct', NL,
        ENDIF, NL, ENDIF, NL])

def classic_time_get_script():
    # rt_classic_time_get -> RETURN rt_ct = [ClassicMode] RunTimer in seconds: missing or below 1 -> 120, above 3599 -> 3599
    from qbasm import asm, S, NL, IF, ENDIF, ENDSCRIPT, RETURN
    return asm([NL,
        'RT_GetINIValue', 'SECTION', '=', S(CT_SECTION), 'key', '=', S(CT_KEY), 'default', '=', 120, NL,
        'rt_ct', '=', '<value', NL,
        IF, '(', '<rt_ct', '<', 1, ')', NL, 'rt_ct', '=', 120, NL, ENDIF, NL,
        IF, '(', '<rt_ct', '>', 3599, ')', NL, 'rt_ct', '=', 3599, NL, ENDIF, NL,
        RETURN, 'rt_ct', '=', '<rt_ct', NL, ENDSCRIPT])

def classic_time_label_script():
    # rt_classic_time_label secs=<n> -> RETURN rt_lbl = "M:SS" for a preset, else "Custom (M:SS)"
    from qbasm import asm, S, NL, IF, ENDIF, ENDSCRIPT, RETURN
    body = [NL, 'Mod', 'a', '=', '<secs', 'b', '=', 60, NL,
        'rt_m', '=', '(', '(', '<secs', '-', '<Mod', ')', '/', 60, ')', NL, 'CastToInteger', 'rt_m', NL, 'rt_s', '=', '<Mod', NL] + \
        pad2('rt_sp', 'rt_s', '0%a', '%a') + [
        'FormatText', 'TextName', '=', 'rt_t', S('%m:%s'), 'm', '=', '<rt_m', 's', '=', '<rt_sp', NL,
        'FormatText', 'TextName', '=', 'rt_lbl', S('Custom (%a)'), 'a', '=', '<rt_t', NL]
    for p in CT_PRESETS:
        body += [IF, '(', '<secs', '=', p, ')', NL, 'rt_lbl', '=', '<rt_t', NL, ENDIF, NL]
    return asm(body + [RETURN, 'rt_lbl', '=', '<rt_lbl', NL, ENDSCRIPT])

def classic_time_menu_script():
    # rt_classic_time_menu id=<row> [left]: the row's left/right handler. Right = next preset above (wraps to 2:00), left = next preset
    # below (wraps to 59:59); a custom ini value steps to its neighbours. Writes the ini and redraws the value like the stock rows.
    from qbasm import asm, S, NL, IF, ELSE, ENDIF, ENDSCRIPT, RETURN
    lft = ['rt_new', '=', CT_PRESETS[-1], NL]
    for p in CT_PRESETS:
        lft += [IF, '(', '<rt_ct', '>', p, ')', NL, 'rt_new', '=', p, NL, ENDIF, NL]
    rgt = ['rt_new', '=', CT_PRESETS[0], NL]
    for p in reversed(CT_PRESETS):
        rgt += [IF, '(', '<rt_ct', '<', p, ')', NL, 'rt_new', '=', p, NL, ENDIF, NL]
    lock = [IF, '(', '<rt_lock', '=', 1, ')', NL,       # hardcore run: nothing changes, the value is redrawn as locked
                '$5a7f4f1a', 'id', '=', '<id', 'text', '=', '<rt_lbl', '$b40120fb', '=', '<rt_lc', NL, RETURN, NL, ENDIF, NL]
    return asm([NL, 'rt_classic_time_locked', NL] + (lock if CLASSIC_HC_LOCK else []) + ['rt_classic_time_get', NL,
        IF, 'GotParam', 'left', NL] + lft + [ELSE, NL] + rgt + [ENDIF, NL,
        'RT_SetINIValue', 'SECTION', '=', S(CT_SECTION), 'key', '=', S(CT_KEY), 'value', '=', '<rt_new', NL,
        'rt_classic_time_label', 'secs', '=', '<rt_new', NL,
        'Printf', S('RTCT menu set %a s'), 'a', '=', '<rt_new', NL,
        IF, 'GotParam', 'left', NL,
            'PlaySound', '$997baa9b', 'Vol', '=', 50.0, 'Pitch', '=', 80.0, NL,
            '$5a7f4f1a', 'id', '=', '<id', 'text', '=', '<rt_lbl', '$1f5cbb2a', 'left', NL,
        ELSE, NL,
            'PlaySound', '$997baa9b', 'Vol', '=', 50.0, 'Pitch', '=', 100.0, NL,
            '$5a7f4f1a', 'id', '=', '<id', 'text', '=', '<rt_lbl', '$1f5cbb2a', 'Right', NL,
        ENDIF, NL, ENDSCRIPT])

def classic_time_locked_script():
    # rt_classic_time_locked -> RETURN rt_lock = 1 in a hardcore Classic run (not at the main menu, where no run is going), plus the row's
    # locked label rt_lbl and dimmed colour rt_lc; rt_lock = 0 otherwise
    from qbasm import asm, S, NL, IF, ENDIF, ENDSCRIPT, RETURN, NOT
    return asm([NL, 'rt_lock', '=', 0, NL] + ([
        IF, NOT, 'InFrontend', NL, IF, 'GameModeEquals', '$5a2c589a', NL, IF, '$b21bafd2', NL, 'rt_lock', '=', 1, NL, ENDIF, NL, ENDIF, NL, ENDIF, NL]
        if CLASSIC_HC_LOCK else []) + [
        RETURN, 'rt_lock', '=', '<rt_lock', 'rt_lbl', '=', S('2:00 (Hardcore)'), 'rt_lc', '=', '[', 128, 128, 128, 48, ']', NL, ENDSCRIPT])

def classic_hide_label_tokens():
    from qbasm import S, NL, IF, ELSE, ENDIF
    return ['RT_GetINIValue', 'SECTION', '=', S(CT_SECTION), 'key', '=', S(CT_HIDE_KEY), 'default', '=', 0, NL,
            'rt_hd', '=', '<value', NL, IF, '(', '<rt_hd', '=', 1, ')', NL, 'rt_hl', '=', S('Hide'), NL, ELSE, NL, 'rt_hl', '=', S('On'), NL, ENDIF, NL]

def classic_hide_menu_script():
    # rt_classic_hide_menu id=<row> [left]: Classic Timer Display row; left/right both toggle On <-> Hide ([ClassicMode] HideTimer 0/1)
    from qbasm import asm, S, NL, IF, ELSE, ENDIF, ENDSCRIPT
    return asm([NL] + classic_hide_label_tokens() + [
        IF, '(', '<rt_hd', '=', 1, ')', NL, 'rt_hd', '=', 0, NL, ELSE, NL, 'rt_hd', '=', 1, NL, ENDIF, NL,
        'RT_SetINIValue', 'SECTION', '=', S(CT_SECTION), 'key', '=', S(CT_HIDE_KEY), 'value', '=', '<rt_hd', NL,
        'Printf', S('RTCT menu hide timer %a'), 'a', '=', '<rt_hd', NL] + classic_hide_label_tokens() + [
        IF, 'GotParam', 'left', NL,
            'PlaySound', '$997baa9b', 'Vol', '=', 50.0, 'Pitch', '=', 80.0, NL,
            '$5a7f4f1a', 'id', '=', '<id', 'text', '=', '<rt_hl', '$1f5cbb2a', 'left', NL,
        ELSE, NL,
            'PlaySound', '$997baa9b', 'Vol', '=', 50.0, 'Pitch', '=', 100.0, NL,
            '$5a7f4f1a', 'id', '=', '<id', 'text', '=', '<rt_hl', '$1f5cbb2a', 'Right', NL,
        ENDIF, NL, ENDSCRIPT])

def classic_hide_loop_script():
    # rt_classic_hide_loop: spawned at a Classic run's start when HideTimer = 1; keeps the native goal timer (the_time) hidden every frame,
    # as speedrun's clock loop does (menus may show it again). Killed when the run goal deactivates.
    from qbasm import asm, NL, IF, ENDIF, BEGIN, REPEAT, ENDSCRIPT
    return asm([NL, BEGIN, NL,
        IF, 'ScreenElementExists', 'id', '=', 'the_time', NL, 'SetScreenElementProps', 'id', '=', 'the_time', 'hide', NL, ENDIF, NL,
        'Wait', 1, 'gameframe', NL, REPEAT, NL, ENDSCRIPT])

def patch_classic_hide(d):
    # TimedScoreRun activate ($0ba17ce8; speedrun's clock hook already sits there): in plain Classic with HideTimer = 1, spawn rt_classic_hide_loop.
    # Deactivate ($6c3f3e25): kill it (the next run's native start shows the timer again).
    from qbasm import asm, S, NL, IF, ENDIF
    d = add_script(d, h('rt_classic_hide_loop'), classic_hide_loop_script())
    start = asm(['KillSpawnedScript', 'name', '=', 'rt_classic_hide_loop', NL,
        IF, '(', 'rt_speedrun_on', '=', 0, ')', NL, IF, 'GameModeEquals', '$5a2c589a', NL,
            'RT_GetINIValue', 'SECTION', '=', S(CT_SECTION), 'key', '=', S(CT_HIDE_KEY), 'default', '=', 0, NL,
            IF, '(', '<value', '=', 1, ')', NL, 'SpawnScriptLater', 'rt_classic_hide_loop', NL, ENDIF, NL,
            'Printf', S('RTCT run start hide timer %a'), 'a', '=', '<value', NL,
        ENDIF, NL, ENDIF, NL])
    stop = asm(['KillSpawnedScript', 'name', '=', 'rt_classic_hide_loop', NL])
    for sh, add, old in ((TSR_ACTIVATE, start, 'rt_old_tsr_activate_ct'), (TSR_DEACTIVATE, stop, 'rt_old_tsr_deactivate_ct')):
        p = next(p for p, t, n in items(d) if t == 7 and n == sh)
        s = script_at(d, p); assert s[0] == 1
        d = relocate_script(d, sh, s[:1] + a4(add) + s[1:], h(old))
    return d

def classic_time_menu_init_script():
    # rt_classic_time_menu_init: General Options' $dfe598ff callback (runs after the rows exist): first draw of the rows' values
    from qbasm import asm, S, NL, IF, ELSE, ENDIF, ENDSCRIPT
    hide = ['FormatText', 'ChecksumName', '=', 'rt_id', S('blacksub_item_%g'), '$fe2be489', '=', S(CT_HIDE_KEY), NL,
        IF, 'ScreenElementExists', 'id', '=', '<rt_id', NL] + classic_hide_label_tokens() + [
            '$5a7f4f1a', 'id', '=', '<rt_id', 'text', '=', '<rt_hl', '$60716227', NL,
        ENDIF, NL]
    return asm([NL,
        'FormatText', 'ChecksumName', '=', 'rt_id', S('blacksub_item_%g'), '$fe2be489', '=', S(CT_KEY), NL,
        IF, 'ScreenElementExists', 'id', '=', '<rt_id', NL,
            'rt_classic_time_get', NL, 'rt_classic_time_locked', NL,
            IF, '(', '<rt_lock', '=', 1, ')', NL,
                '$5a7f4f1a', 'id', '=', '<rt_id', 'text', '=', '<rt_lbl', '$b40120fb', '=', '<rt_lc', '$60716227', NL,
            ELSE, NL,
                'rt_classic_time_label', 'secs', '=', '<rt_ct', NL,
                '$5a7f4f1a', 'id', '=', '<rt_id', 'text', '=', '<rt_lbl', '$60716227', NL,
            ENDIF, NL,
        ENDIF, NL] + (hide if CLASSIC_HIDE else []) + [ENDSCRIPT])

def patch_options_row(d):
    # gameplayoptions $372953c5 (General Options): an inline option struct right after End Run Action ($af5cf3b8). The row builder
    # $3d701618 takes structs (IsStructure Option); $3038eff8 = custom left/right scripts. The stock value helpers look options up in
    # $11d7ad9d, so the value is drawn by our own $dfe598ff callback (the builder runs it after the rows are made).
    from qbasm import asm, S, NL
    from qbsplice import splice
    N = lambda x: b'\x16' + struct.pack('<I', x)
    p = next(p for p, t, n in items(d) if t == 7 and n == 0x372953c5)
    s = script_at(d, p)
    title = b'\x1b' + struct.pack('<I', 16) + b'General Options\0'
    assert s.count(title) == 1 and s.count(N(0xaf5cf3b8)) == 1
    t = s.index(title) + len(title); assert s[t] == 1
    cb = asm(['$dfe598ff', '=', 'rt_classic_time_menu_init'])
    i = s.index(N(0xaf5cf3b8)) + 5; assert s[i] == 1 and i < t
    desc = ('Length of a Classic Mode run.\\n\\nPresets: 2:00, 5:00, 10:00, 20:00, 59:59.\\nAny time up to 59:59 can be set in rethawed.ini, '
            '[ClassicMode] RunTimer=seconds.\\n\\nApplies from the next level load. Speedrun Mode is not affected.')
    info = [] if os.environ.get('CT_BISECT') == 'nodesc' else ['Description', '=', S(desc), 'tIP', '=', S('Length of a Classic Mode run.')]
    row = asm([NL, '{', 'key', '=', S(CT_KEY), 'NAME', '=', S('Classic Run Timer')] + info + ['$3038eff8', 'rgba', '=', '[', 128, 128, 128, 128, ']',
        '$adba2e16', '=', 'rt_classic_time_menu', '$3ef5a7df', '=', '{', 'left', '}',
        '$ebc0d000', '=', 'rt_classic_time_menu', '$788f59c9', '=', '{', '}', '}'])
    if CLASSIC_HIDE:
        hdesc = ('Shows or hides the timer during a Classic Mode run. The clock keeps running and the run still ends on time.\\n\\n'
                 'Applies from the next run. Speedrun Mode is not affected.')
        row += asm([NL, '{', 'key', '=', S(CT_HIDE_KEY), 'NAME', '=', S('Classic Timer Display'), 'Description', '=', S(hdesc),
            'tIP', '=', S('Show or hide the Classic Mode timer.'), '$3038eff8', 'rgba', '=', '[', 128, 128, 128, 128, ']',
            '$adba2e16', '=', 'rt_classic_hide_menu', '$3ef5a7df', '=', '{', 'left', '}',
            '$ebc0d000', '=', 'rt_classic_hide_menu', '$788f59c9', '=', '{', '}', '}'])
    # QB arrays are homogeneous: a struct among the option names is misread (v46 first try: crash / hang as soon as the page opened).
    # So every name becomes a wrapper struct { $583be290 = <name> }, the form rt_autoserver's page uses; $3d701618 unwraps it via $7dcf9c17.
    a = s.index(b'\x07\x05', s.index(N(h('options')))) + 2
    e = s.index(b'\x06', i); assert e < t and s[e+1] == 0x16
    def wrap(seg):
        out = bytearray(); k = 0
        while k < len(seg):
            if seg[k] == 1: out += b'\x01'; k += 1
            else:
                assert seg[k] == 0x16; out += b'\x03' + N(0x583be290) + b'\x07' + seg[k:k+5] + b'\x04'; k += 5
        return bytes(out)
    body = wrap(s[a:i]) + row + wrap(s[i:e])
    body += b'\x01' * ((-(len(body) - (e - a) + len(cb))) % 4)     # whole edit a multiple of 4 (newlines can only go inside the array)
    s = splice(s, a, t, body + s[e:t] + cb)       # one splice: the two edits are 4-aligned only together
    d = add_script(d, h('rt_classic_time_label'), classic_time_label_script())
    d = add_script(d, h('rt_classic_time_menu'), classic_time_menu_script())
    d = add_script(d, h('rt_classic_time_menu_init'), classic_time_menu_init_script())
    d = add_script(d, h('rt_classic_time_locked'), classic_time_locked_script())
    if CLASSIC_HIDE:
        d = add_script(d, h('rt_classic_hide_menu'), classic_hide_menu_script())
    return relocate_script(d, 0x372953c5, s, h('rt_old_general_options'))

CLOCK_SCRIPT = h('rt_speedrun_clock'); CLOCK_ID = h('rt_sr_clock')
TSR_ACTIVATE = 0x0ba17ce8; TSR_DEACTIVATE = 0x6c3f3e25

def clock_script():
    # count-up HUD: M:SS.cc, accumulated per frame from GetStartTime (current time, ms) and skipped while GameIsPaused
    from qbasm import asm, S, P, NL, NOT, BEGIN, REPEAT, BREAK, ENDSCRIPT, ENDIF, IF, ELSE
    def pad(dst, src):   # dst = src as text, with a leading 0 when < 10
        return [IF, '(', '<' + src, '<', 10, ')', NL, 'FormatText', 'TextName', '=', dst, S('0%a'), 'a', '=', '<' + src, NL,
                ELSE, NL, 'FormatText', 'TextName', '=', dst, S('%a'), 'a', '=', '<' + src, NL, ENDIF, NL]
    theme = os.environ.get('CLOCK_STYLE', 'theme') == 'theme'
    # theme: write into the HUD theme's own timer text ({the_time child=text}, the element panelstuff script $6562ac43 fills);
    #        with unlimited_time the engine stops updating it, so it is ours. own: a separate newtimerfont element rt_sr_clock.
    if theme:
        show = [IF, 'ScreenElementExists', 'id', '=', 'the_time', NL,
                    'ResolveScreenElementID', 'id', '=', '{', 'the_time', 'child', '=', 'text', '}', NL,
                    IF, 'ScreenElementExists', 'id', '=', '<$28e66b14', NL,
                        'SetScreenElementProps', 'id', '=', '<$28e66b14', 'text', '=', '<rt_clk', NL, ENDIF, NL,
                ENDIF, NL]
        head = [NL]; alive = []
    else:
        show = ['SetScreenElementProps', 'id', '=', 'rt_sr_clock', 'text', '=', '<rt_clk', NL]
        alive = [IF, NOT, 'ScreenElementExists', 'id', '=', 'rt_sr_clock', NL, BREAK, NL, ENDIF, NL,
                 # the native goal clock (the_time) stays frozen at the limit with unlimited_time; menus unhide it, so hide it every frame
                 IF, 'ScreenElementExists', 'id', '=', 'the_time', NL, 'SetScreenElementProps', 'id', '=', 'the_time', 'hide', NL, ENDIF, NL]
    prog = (head if theme else [NL,
        IF, 'ScreenElementExists', 'id', '=', 'rt_sr_clock', NL, 'DestroyScreenElement', 'id', '=', 'rt_sr_clock', NL, ENDIF, NL,
        'CreateScreenElement', '{', NL,
            'type', '=', 'TextElement', NL, 'parent', '=', 'root_window', NL, 'id', '=', 'rt_sr_clock', NL,
            'just', '=', '[', 'center', 'top', ']', NL, 'pos', '=', P(float(os.environ.get('CLOCK_X', 320)), float(os.environ.get('CLOCK_Y', 20))), NL,
            'text', '=', S('0:00.00'), NL, 'font', '=', 'newtimerfont', NL, 'scale', '=', float(os.environ.get('CLOCK_SCALE', 0.9)), NL,
            'rgba', '=', '[', 128, 128, 128, 128, ']', NL, 'z_priority', '=', 100, NL,
        '}', NL]) + [
        # the Classic goal-cam intro ($397c3e70) is spawned right after us and raises $90fa17c6 until it ends ($f59623a3)
        'Wait', 2, 'gameframe', NL,
        'GetStartTime', NL, 'rt_last', '=', '<StartTime', NL, 'rt_tot', '=', 0, NL, 'rt_pf', '=', 0, NL,
        BEGIN, NL,
            'Wait', 1, 'gameframe', NL] + alive + [
            'GetStartTime', NL,
            IF, NOT, 'GameIsPaused', NL,
                IF, '(', '$90fa17c6', '=', 0, ')', NL] + ([IF, '(', 'rt_sr_done', '=', 0, ')', NL] if FINISH else []) + [
                    'rt_tot', '=', '(', '<rt_tot', '+', '(', '<StartTime', '-', '<rt_last', ')', ')', NL] + ([ENDIF, NL] if FINISH else []) + [
                ENDIF, NL,
            ENDIF, NL,
            'rt_last', '=', '<StartTime', NL,
            'Mod', 'a', '=', '<rt_tot', 'b', '=', 1000, NL,
            'rt_cs', '=', '(', '<Mod', '/', 10, ')', NL, 'CastToInteger', 'rt_cs', NL,
            'rt_sec', '=', '(', '(', '<rt_tot', '-', '<Mod', ')', '/', 1000, ')', NL, 'CastToInteger', 'rt_sec', NL,
            'Mod', 'a', '=', '<rt_sec', 'b', '=', 60, NL,
            'rt_min', '=', '(', '(', '<rt_sec', '-', '<Mod', ')', '/', 60, ')', NL, 'CastToInteger', 'rt_min', NL,
            'rt_sec', '=', '<Mod', NL] + pad('rt_sp', 'rt_sec') + (pad('rt_cp', 'rt_cs') + [
            'FormatText', 'TextName', '=', 'rt_clk', S('%m:%s.%c'), 'm', '=', '<rt_min', 's', '=', '<rt_sp', 'c', '=', '<rt_cp', NL]
            if os.environ.get('CLOCK_CS') else [
            # M:SS like the base timer ($6562ac43): minutes get a leading 0 when the theme option $f89fcbe9 is on
            IF, '(', '$f89fcbe9', '=', 1, ')', NL] + pad('rt_mp', 'rt_min') + [ELSE, NL,
                'FormatText', 'TextName', '=', 'rt_mp', S('%a'), 'a', '=', '<rt_min', NL, ENDIF, NL,
            'FormatText', 'TextName', '=', 'rt_clk', S('%m:%s'), 'm', '=', '<rt_mp', 's', '=', '<rt_sp', NL]) + [
            ] + show + ([ 'change', 'rt_sr_final', '=', '<rt_clk', NL] if FINISH and not SR_GLOBAL else []) + (
            # elapsed run time in ms for the finish (and later best times); ExpireGoal kills this script in the same frame
            ['change', 'rt_sr_ms', '=', '<rt_tot', NL] if FINISH and SR_GLOBAL else []) + [
        REPEAT, NL, ENDSCRIPT]
    return asm(prog)

FINISH = bool(os.environ.get('FINISH_ON_ALL'))
GOAL_LIST = '$c7ebd48f'; GOAL_NAME = '$7c1956af'

def won_script():
    # rt_speedrun_goal_won goal_id=<won goal>: when every other goal of the level (except the run's TimedScoreRun) is beaten, finish
    from qbasm import asm, NL, NOT, BEGIN, REPEAT, ENDSCRIPT, ENDIF, IF, RETURN as RET
    if SR_GLOBAL:
        # Classic's own "level complete" test (classic_mode_menu $09b2606c): GetClassicLevelGoalCount -> $1af41962 goals done in this
        # level, RTCM_LevelGetPotentialGoals -> $57eea529 goals in it (10). Cash pickups etc. are not counted.
        # Finish = what Pause > End Run does ($d5ccb8af): GoalManager_ExpireGoal on the run goal ($7c5db638) -> Classic end of run, no overtime.
        return asm([NL,
            'Wait', 10, 'gameframe', NL,              # let RTCM record the goal first
            IF, '(', 'rt_sr_done', '=', 1, ')', NL, RET, NL, ENDIF, NL,
            'GetClassicLevelGoalCount', NL,
            '$57eea529', '=', 0, NL, 'GetLevelStructureName', NL,
            'RTCM_LevelGetPotentialGoals', 'level', '=', '(', '<$63b1214e', '.', '$893d9170', ')', NL,
            IF, '(', '<$57eea529', '>', 0, ')', NL,
                IF, '(', '<$1af41962', '=', '<$57eea529', ')', NL,
                    'change', 'rt_sr_done', '=', 1, NL,
                    'GoalManager_ExpireGoal', 'name', '=', '(', '$7c5db638', ')', NL,
                ENDIF, NL,
            ENDIF, NL, ENDSCRIPT])
    prog = [NL,
        IF, '(', 'rt_sr_done', '=', 1, ')', NL, RET, NL, ENDIF, NL,
        IF, 'ChecksumEquals', 'a', '=', '<goal_id', 'b', '=', '(', 'rt_sr_goal', ')', NL, RET, NL, ENDIF, NL,
        'GoalManager_GetGoalList', NL, 'GetArraySize', '<' + GOAL_LIST, NL,
        'rt_i', '=', 0, NL, 'rt_all', '=', 1, NL,
        BEGIN, NL,
            'rt_g', '=', '(', '<' + GOAL_LIST, '[', '<rt_i', ']', '.', GOAL_NAME, ')', NL,
            IF, NOT, 'ChecksumEquals', 'a', '=', '<rt_g', 'b', '=', '(', 'rt_sr_goal', ')', NL,
                IF, NOT, 'ChecksumEquals', 'a', '=', '<rt_g', 'b', '=', '<goal_id', NL,
                    IF, NOT, 'GoalManager_HasWonGoal', 'name', '=', '<rt_g', NL, 'rt_all', '=', 0, NL, ENDIF, NL,
                ENDIF, NL,
            ENDIF, NL,
            'rt_i', '=', '(', '<rt_i', '+', 1, ')', NL,
        REPEAT, '<array_size', NL,
        IF, '(', '<rt_all', '=', 1, ')', NL,
            # stop the count-up, then end the run the native way: the TimedScoreRun expires -> Classic end-of-run flow
            'change', 'rt_sr_done', '=', 1, NL,
            'GoalManager_RemoveUnlimitedTimeFlag', 'name', '=', '(', 'rt_sr_goal', ')', NL,
            'GoalManager_ZeroGoalTimer', 'name', '=', '(', 'rt_sr_goal', ')', NL,
        ENDIF, NL, ENDSCRIPT]
    return asm(prog)

RESET_GOALS = bool(os.environ.get('RESET_GOALS'))     # step 4: unbeat the level's goals at every speedrun run start; finish on our own count

def goal_loop(body):
    """asm tokens: for each goal of the level (GoalManager_GetGoalList), with its name in <rt_g>"""
    from qbasm import NL, BEGIN, REPEAT, IF, ENDIF
    return ['GoalManager_GetGoalList', NL, 'GetArraySize', '<' + GOAL_LIST, NL, 'rt_i', '=', 0, NL,
            IF, '(', '<array_size', '>', 0, ')', NL,
            BEGIN, NL,
                'rt_g', '=', '(', '<' + GOAL_LIST, '[', '<rt_i', ']', '.', GOAL_NAME, ')', NL] + body + [
                'rt_i', '=', '(', '<rt_i', '+', 1, ')', NL,
            REPEAT, '<array_size', NL,
            ENDIF, NL]

FLAG_RESET = bool(os.environ.get('FLAG_RESET'))   # v48: clear every goal_flags entry of each goal at speedrun run start (see clear_flags_script)

def clear_flags_script():
    # rt_speedrun_clear_flags goal=<g>: flag goals (gap/trick-gap goals, e.g. "Kickflip over TC's Roof Gap": the gap's TrickScript does
    # GoalManager_SetGoalFlag got_1 1 and the goal wins when all goal_flags are set). Classic never re-activates a won goal, but speedrun
    # UnBeats and re-activates every goal, and stock Retry only resets goals that are still active, so a goal won earlier in the session
    # can come back with its flags still set and never win again. Zero every flag that is still set (logged, so debug.txt shows it).
    from qbasm import asm, S, NL, NOT, IF, ENDIF, BEGIN, REPEAT, ENDSCRIPT, RETURN
    return asm([NL,
        # gap goals only (goal_gaps init $571fd599): they have no pickups, so a cleared flag is simply earned again. Collect / letter goals
        # are left to rt_speedrun_reset_collect, which re-creates their pickups together with their flags.
        'GoalManager_GetGoalParams', 'name', '=', '<goal', NL,
        IF, NOT, 'GotParam', 'init', NL, RETURN, NL, ENDIF, NL,
        IF, NOT, 'ChecksumEquals', 'a', '=', '<init', 'b', '=', '$571fd599', NL, RETURN, NL, ENDIF, NL,
        IF, 'GotParam', 'goal_flags', NL,
            'GetArraySize', '<goal_flags', NL, 'rt_f', '=', 0, NL,
            IF, '(', '<array_size', '>', 0, ')', NL, BEGIN, NL,
                'rt_fl', '=', '(', '<goal_flags', '[', '<rt_f', ']', ')', NL,
                IF, 'GoalManager_GoalFlagSet', 'name', '=', '<goal', 'Flag', '=', '<rt_fl', NL] + (
                    ['Printf', S('RTSR stale flag %a on %b, clearing'), 'a', '=', '<rt_fl', 'b', '=', '<goal', NL] if SR_LOG else []) + [
                    'GoalManager_SetGoalFlag', 'name', '=', '<goal', '<rt_fl', 0, NL,
                ENDIF, NL,
                'rt_f', '=', '(', '<rt_f', '+', 1, ')', NL,
            REPEAT, '<array_size', NL, ENDIF, NL,
        ENDIF, NL, ENDSCRIPT])

def patch_runstart(d):
    # classic_mode_menu $d73f1a90 starts every Classic run (GoalManager_ActivateAllGoals). In speedrun, first unbeat every goal of
    # the level, so each run starts at 0/10. GoalManager state only: Classic's tour progress lives in RTCM (re-synced at level load).
    from qbasm import asm, NL, S
    p = next(p for p, t, n in items(d) if t == 7 and n == 0xd73f1a90)
    s = script_at(d, p); assert s[0] == 1
    if RESET_GOALS and RUN_END and COLLECT_RESET:
        d = add_script(d, h('rt_speedrun_reset_collect'), collect_reset_script())
    if RESET_GOALS and FLAG_RESET:
        d = add_script(d, h('rt_speedrun_clear_flags'), clear_flags_script())
    body = []
    if RESET_GOALS:
        from qbasm import IF, ENDIF
        cr = RUN_END and COLLECT_RESET
        if cr:
            # v36b-v38 re-created the collect pickups at EVERY run start, so a fresh level got two of each pilot wing (Evan). Only do it on a
            # Retry right after a finished run of this same level (set at the finish: rt_sr_retry = 1, rt_sr_lvl = level); stock Retry already
            # handles an unfinished run.
            body += ['rt_rl', '=', 0, NL, 'GetCurrentLevel', NL,
                     IF, '(', 'rt_sr_retry', '=', 1, ')', NL,
                         'rt_rl', '=', 1, NL,
                     ENDIF, NL] + (['Printf', S('RTSR retry check retry=%a same-level=%b'), 'a', '=', '(', 'rt_sr_retry', ')', 'b', '=', '<rt_rl', NL,
                       'Printf', S('RTSR level now %g stored %h'), 'g', '=', '<level', 'h', '=', '(', 'rt_sr_lvl', ')', NL] if SR_LOG else []) + [
                     'change', 'rt_sr_retry', '=', 0, NL]
        body += goal_loop(([IF, '(', '<rt_rl', '=', 1, ')', NL, 'rt_speedrun_reset_collect', 'goal', '=', '<rt_g', NL, ENDIF, NL] if cr else []) + ['GoalManager_UnBeatGoal', 'name', '=', '<rt_g', NL]
                          + (['rt_speedrun_clear_flags', 'goal', '=', '<rt_g', NL] if FLAG_RESET else [])) + (['Printf', S('RTSR goals reset'), NL] if SR_LOG else [])
    if STATS_MAX:
        # skutils $d73dfe2e value=N wraps the native SetStatOverride (session only, nothing saved). Classic uses it per level
        # (level_params classic stats, default 10) and load_level resets it to 0, so it never leaks past the level.
        body += ['$d73dfe2e', 'value', '=', STATS_MAX, NL] + (['Printf', S('RTSR stats %a'), 'a', '=', STATS_MAX, NL] if SR_LOG else [])
    add = asm(sr_wrap(body))
    return relocate_script(d, 0xd73f1a90, s[:1] + a4(add) + s[1:], h('rt_old_classic_run_start'))

CASH_OFF = bool(os.environ.get('CASH_OFF'))           # THPS2 cash pickups are not created in speedrun
STATS_MAX = int(os.environ.get('STATS_MAX', 0))        # speedrun: all skater stats overridden to this value at run start (10 = full)
STATS_OFF = bool(os.environ.get('STATS_OFF'))          # speedrun: no stat-point pickups

def patch_statnodes(d):
    # stats.qb $adf5d19b ("Looking for classic stat nodes...", run from load_level) creates the stat-point pickups. In speedrun: return first.
    from qbasm import asm, NL, RETURN
    p = next(p for p, t, n in items(d) if t == 7 and n == 0xadf5d19b)
    s = script_at(d, p); assert s[0] == 1
    return relocate_script(d, 0xadf5d19b, s[:1] + a4(asm(sr_wrap([RETURN, NL]))) + s[1:], h('rt_old_stat_nodes'))

HIDE_ALLGOALS = bool(os.environ.get('HIDE_ALLGOALS'))  # speedrun: THPS2's "100% Goals and Cash" goal is never added

def patch_allgoals(d):
    # goal_all_goals $94b89390 adds the AllGoalsAndCash goal (it starts with a default-param struct). In speedrun: return before AddGoal,
    # so the goal isn't in the list, the goal screens or the count. Classic's $7bd8e8dd then finds no all-goals goal (GotParam fails).
    from qbasm import asm, NL, RETURN
    p = next(p for p, t, n in items(d) if t == 7 and n == 0x94b89390)
    s = script_at(d, p); assert s[0] == 3
    i = s.index(b'\x04\x01') + 2
    return relocate_script(d, 0x94b89390, s[:i] + a4(asm(sr_wrap([RETURN, NL]))) + s[i:], h('rt_old_add_allgoals'))

def patch_cash(d):
    # goal_all_goals $f2586ab7 creates one cash pickup node (returns true/false). In speedrun: RETURN False first, so no cash spawns.
    from qbasm import asm, NL, RETURN
    p = next(p for p, t, n in items(d) if t == 7 and n == 0xf2586ab7)
    s = script_at(d, p); assert s[0] == 1
    add = asm(sr_wrap([RETURN, 'False', NL]))
    return relocate_script(d, 0xf2586ab7, s[:1] + a4(add) + s[1:], h('rt_old_cash_node'))

def patch_levelcomplete(d):
    if RESET_GOALS:
        return patch_levelcomplete_own(d)
    return patch_levelcomplete_rtcm(d)

def patch_levelcomplete_own(d):
    # Count this run's wins ourselves (Classic's RTCM count includes goals beaten in the save). Prepended to $ea893bed (runs on every
    # Classic goal success, before its own early return). A goal counts if it is won, or still to do if active; the run goal is skipped.
    from qbasm import asm, NL, NOT, IF, ENDIF, S
    p = next(p for p, t, n in items(d) if t == 7 and n == 0xea893bed)
    s = script_at(d, p); assert s[0] == 1
    # THPS2-tour levels also have "100% Goals and Cash" (goal_all_goals, flagged $62de0ef4): not counted, so no cash is needed.
    # Detected like Classic does it ($7bd8e8dd): RemoveParameter $62de0ef4, then $5dc79c57 goal=<g> returns the flag if set.
    body = [IF, NOT, 'ChecksumEquals', 'a', '=', '<rt_g', 'b', '=', '(', '$7c5db638', ')', NL,
              'RemoveParameter', '$62de0ef4', NL, '$5dc79c57', 'goal', '=', '<rt_g', NL,
              IF, NOT, 'GotParam', '$62de0ef4', NL,
                IF, 'GoalManager_HasWonGoal', 'name', '=', '<rt_g', NL, 'rt_w', '=', '(', '<rt_w', '+', 1, ')', NL,
                    'rt_n', '=', '(', '<rt_n', '+', 1, ')', NL,
                ELSE_, NL,
                    IF, 'GoalManager_GoalIsActive', 'name', '=', '<rt_g', NL, 'rt_n', '=', '(', '<rt_n', '+', 1, ')', NL, ENDIF, NL,
                ENDIF, NL,
              ENDIF, NL,
            ENDIF, NL]
    from qbasm import ELSE
    body = [ELSE if t is ELSE_ else t for t in body]
    add = asm(sr_wrap([IF, '(', 'rt_sr_done', '=', 0, ')', NL, 'rt_w', '=', 0, NL, 'rt_n', '=', 0, NL] + goal_loop(body) +
                      (['Printf', S('RTSR goals %a of %b'), 'a', '=', '<rt_w', 'b', '=', '<rt_n', NL] if SR_LOG else []) +
                      [IF, '(', '<rt_n', '>', 0, ')', NL, IF, '(', '<rt_w', '=', '<rt_n', ')', NL,   # not '>=': the 0x15 token hangs/crashes this engine (v21, v23t)
                          'change', 'rt_sr_done', '=', 1, NL] + (['rt_speedrun_save_best', NL] if SAVE_BEST else []) +
                      (unpark_tokens() if PARK_TOUR else []) +
                      (['Printf', S('RTSR finished final time %a ms'), 'a', '=', '(', 'rt_sr_ms', ')', NL] if SR_LOG else []) +
                      end_tokens() + [ENDIF, NL, ENDIF, NL,
                      ENDIF, NL] + (['Printf', S('RTSR count end'), NL] if SR_LOG else [])))
    return relocate_script(d, 0xea893bed, s[:1] + a4(add) + s[1:], h('rt_old_level_complete'))

ELSE_ = object()

def end_tokens():
    """finish: end the run (Pause > End Run's ExpireGoal); with RUN_END a new best first shows NEW BEST TIME!! (rt_speedrun_newbest ends it)"""
    from qbasm import NL, IF, ELSE, ENDIF
    expire = ['GoalManager_ExpireGoal', 'name', '=', '(', '$7c5db638', ')', NL]
    if not (RUN_END and SAVE_BEST):
        return expire
    # the popup waits for the post-run menu (patch_pause_best reads and clears rt_sr_nb)
    return ['change', 'rt_sr_nb', '=', 0 if os.environ.get('NOBEST_TEST') else '<rt_newbest', NL, 'change', 'rt_sr_run', '=', '(', 'rt_sr_ms', ')', NL, 'change', 'rt_sr_has', '=', 1, NL] + (
        ['change', 'rt_sr_retry', '=', 1, NL] if COLLECT_RESET else []) + expire

def patch_levelcomplete_rtcm(d):
    # v22: Classic's own level-complete check ($ea893bed, classic_mode_menu; spawned from goal_success on every Classic goal) already
    # detects 10/10 and then calls the achievement script $2a8edc69. Right after that call, in speedrun: stop the clock and End Run.
    # (v21's separate per-goal check crashed the game on goal completion.)
    from qbasm import asm, NL
    # Appended at the end (top level, so no enclosing IF skip offsets change): the script returns early when its flag $617093d4
    # was already set, so reaching the end with the flag set means 10/10 was reached just now.
    from qbasm import IF, ENDIF
    p = next(p for p, t, n in items(d) if t == 7 and n == 0xea893bed)
    s = script_at(d, p); assert s.endswith(b'\x28\x01\x24')
    from qbasm import S
    logc = (['Printf', S('RTSR goals %a of %b'), 'a', '=', '<$1af41962', 'b', '=', '<$57eea529', NL] if SR_LOG else [])
    logf = (['Printf', S('RTSR finished final time %a ms'), 'a', '=', '(', 'rt_sr_ms', ')', NL] if SR_LOG else [])
    add = asm(sr_wrap(logc + [IF, '(', '$617093d4', '=', 1, ')', NL, IF, '(', 'rt_sr_done', '=', 0, ')', NL,
                       'change', 'rt_sr_done', '=', 1, NL] + logf + ['GoalManager_ExpireGoal', 'name', '=', '(', '$7c5db638', ')', NL,
                       ENDIF, NL, ENDIF, NL]))
    return relocate_script(d, 0xea893bed, s[:-1] + a4(add) + b'\x24', h('rt_old_level_complete'))

def current_tour_tokens(dst):
    """asm tokens: <dst> = id of the current Classic tour (RTCM_GetTourList + RTCM_TourIs)"""
    from qbasm import NL, BEGIN, REPEAT, ENDIF, IF
    TL = '$9a07e7c7'
    return ['RTCM_GetTourList', NL, 'GetArraySize', '<' + TL, NL, 'rt_ti', '=', 0, NL, dst, '=', 0, NL,
            IF, '(', '<array_size', '>', 0, ')', NL,
            BEGIN, NL,
                'rt_t', '=', '(', '(', '<' + TL, '[', '<rt_ti', ']', ')', '.', 'id', ')', NL,
                IF, 'RTCM_TourIs', '<rt_t', NL, dst, '=', '<rt_t', NL, ENDIF, NL,
                'rt_ti', '=', '(', '<rt_ti', '+', 1, ')', NL,
            REPEAT, '<array_size', NL, ENDIF, NL]

PARK_TOUR = bool(os.environ.get('PARK_TOUR'))
TOUR_DIFF = bool(os.environ.get('TOUR_DIFF'))   # v49b: THPS3 street/vert goal names use the real tour's Sick setting while it is parked

def patch_tour_diff(d):
    # goal_utilities $4a0a152c picks a THPS3 level's street/vert goals with `IF $6c197ad0` (tour is Sick -> both goals, versioned names).
    # Goals are added at level load (real tour), but the gap TrickScripts ($ae79efc9 -> $4a0a152c) rebuild the goal name at gap time, when
    # a speedrun run has the tour parked on a decoy -> wrong name -> Foundry's Indy gap never counted. Only that call is redirected to
    # rt_speedrun_is_sick (cached real-tour answer while parked). v49 patched $6c197ad0 / $b21bafd2 themselves and hung the game before the
    # main menu (they run during boot, e.g. from the tour callbacks); this version leaves them untouched.
    from qbasm import asm, NL, IF, ENDIF, RETURN, ENDSCRIPT
    import qbrt
    # v49c: no tour lookup at all. Once the level's goals exist, answer from which ones were added (the vert goal is "<prefix>_classic_goal_gaps2"
    # only when both were added), so the gap-time name always matches the load-time goals. Before they exist (level load), and outside
    # speedrun, the stock check runs, so the goal set is exactly what v47 / Classic add.
    from qbasm import S, NOT
    d = add_script(d, h('rt_speedrun_is_sick'), asm([NL,
        IF, '(', 'rt_speedrun_on', '=', 1, ')', NL,
            IF, NOT, 'InNetGame', NL,
                'GetLevelPrefix', NL,
                'FormatText', 'ChecksumName', '=', 'rt_g2', S('%l_classic_goal_gaps2'), '$69f93d01', '=', '<$15f818d0', NL,
                IF, 'GoalManager_GoalExists', 'name', '=', '<rt_g2', NL, RETURN, 'true', NL, ENDIF, NL,
                'FormatText', 'ChecksumName', '=', 'rt_g1', S('%l_classic_goal_gaps'), '$69f93d01', '=', '<$15f818d0', NL,
                IF, 'GoalManager_GoalExists', 'name', '=', '<rt_g1', NL, RETURN, 'False', NL, ENDIF, NL,
            ENDIF, NL,
        ENDIF, NL,
        IF, '$6c197ad0', NL, RETURN, 'true', NL, ENDIF, NL,
        RETURN, 'False', NL, ENDSCRIPT]))
    p = next(p for p, t, n in items(d) if t == 7 and n == 0x4a0a152c)
    sc = script_at(d, p)
    text = qbrt.disassemble(sc)
    assert qbrt.assemble(text) == sc
    assert text.count('IF $6c197ad0') == 1
    new = qbrt.assemble(text.replace('IF $6c197ad0', 'IF rt_speedrun_is_sick'))
    return relocate_script(d, 0x4a0a152c, new, h('rt_old_street_vert_goals'))

def unpark_tokens():
    from qbasm import S, NL, IF, ENDIF
    decoy = (['GetClassicLevelGoalCount', NL, 'Printf', S('RTSR decoy tour progress at restore %a'), 'a', '=', '<$b35f8a59', NL] if SR_LOG else [])
    return [IF, '(', 'rt_sr_parked', '=', 1, ')', NL] + decoy + ['RTCM_ChooseTour', '(', 'rt_sr_tour', ')', NL,
            'change', 'rt_sr_parked', '=', 0, NL, 'Printf', S('RTSR tour restored'), NL] + (
            ['GetClassicLevelGoalCount', NL, 'Printf', S('RTSR classic progress for this level %a'), 'a', '=', '<$1af41962', NL] if SR_LOG else []) + [
            ENDIF, NL]

def tour_off():
    from qbasm import S, NL
    # a missing tour id is a fatal "Could not start classic tour"; use another real tour (THAW has no Hangar)
    return current_tour_tokens('rt_saved_tour') + ['RTCM_ChooseTour', 'THAW', NL, 'Printf', S('RTSR tour off (was %g)'), 'g', '=', '<rt_saved_tour', NL]

def rtcm_probe():
    # TEST: does Classic's tour progress (GetClassicLevelGoalCount -> $1af41962) record a win immediately, and does UnBeatGoal undo it?
    from qbasm import S, NL, NOT, BEGIN, REPEAT, BREAK, ENDIF, IF
    cnt = lambda tag: ['GetClassicLevelGoalCount', NL, 'Printf', S('RTSR rtcm ' + tag + ' %a'), 'a', '=', '<$1af41962', NL]
    return (cnt('before') +
        ['GoalManager_GetGoalList', NL, 'GetArraySize', '<' + GOAL_LIST, NL, 'rt_i', '=', 0, NL, 'rt_pg', '=', 0, NL,
         BEGIN, NL,
            'rt_g', '=', '(', '<' + GOAL_LIST, '[', '<rt_i', ']', '.', GOAL_NAME, ')', NL,
            IF, NOT, 'ChecksumEquals', 'a', '=', '<rt_g', 'b', '=', '(', '$7c5db638', ')', NL,
                IF, 'GoalManager_GoalIsActive', 'name', '=', '<rt_g', NL, 'rt_pg', '=', '<rt_g', NL, BREAK, NL, ENDIF, NL,
            ENDIF, NL,
            'rt_i', '=', '(', '<rt_i', '+', 1, ')', NL,
         REPEAT, '<array_size', NL,
        ] + (tour_off() if os.environ.get('TEST_RTCM') == '2' else []) + [
         'GoalManager_WinGoal', 'name', '=', '<rt_pg', NL, 'Wait', 60, 'gameframe', NL] +
        (['RTCM_ChooseTour', '<rt_saved_tour', NL, 'Printf', S('RTSR tour restored'), NL] if os.environ.get('TEST_RTCM') == '2' else []) +
        cnt('after_win') +
        ['GoalManager_UnBeatGoal', 'name', '=', '<rt_pg', NL, 'Wait', 30, 'gameframe', NL] + cnt('after_unbeat') +
        ['change', 'rt_sr_done', '=', 0, NL])

def autowin_script():
    # TEST ONLY: after the goal-cam intro, win every active, unwon goal of the level (except the run's own TimedScoreRun) every ~3 s
    from qbasm import asm, S, NL, NOT, BEGIN, REPEAT, BREAK, ENDSCRIPT, ENDIF, IF
    return asm([NL,
        'Wait', 2, 'gameframe', NL,
        BEGIN, NL, 'Wait', 1, 'gameframe', NL, IF, '(', '$90fa17c6', '=', 0, ')', NL, BREAK, NL, ENDIF, NL, REPEAT, NL,
        'Wait', int(os.environ.get('AUTOWIN_DELAY', 180)), 'gameframe', NL,
        'Printf', S('RTSR autowin start'), NL] + (rtcm_probe() if os.environ.get('TEST_RTCM') else []) + [
        'GoalManager_GetGoalList', NL, 'GetArraySize', '<' + GOAL_LIST, NL, 'rt_i', '=', 0, NL,
        BEGIN, NL,
            IF, '(', 'rt_sr_done', '=', 1, ')', NL, BREAK, NL, ENDIF, NL,
            'rt_g', '=', '(', '<' + GOAL_LIST, '[', '<rt_i', ']', '.', GOAL_NAME, ')', NL,
            IF, NOT, 'ChecksumEquals', 'a', '=', '<rt_g', 'b', '=', '(', '$7c5db638', ')', NL,
                IF, 'GoalManager_GoalIsActive', 'name', '=', '<rt_g', NL,
                    IF, NOT, 'GoalManager_HasWonGoal', 'name', '=', '<rt_g', NL,
                        'Printf', S('RTSR autowin %g'), 'g', '=', '<rt_g', NL,
                        'GoalManager_WinGoal', 'name', '=', '<rt_g', NL,
                        'Wait', int(os.environ.get('AUTOWIN_GAP', 180)), 'gameframe', NL,
                    ENDIF, NL,
                ENDIF, NL,
            ENDIF, NL,
            'rt_i', '=', '(', '<rt_i', '+', 1, ')', NL,
        REPEAT, '<array_size', NL,
        'Printf', S('RTSR autowin done'), NL, ENDSCRIPT])

SAVE_BEST = bool(os.environ.get('SAVE_BEST'))   # store each level's best time on the skater profile (property rt_sr_times)

INI_SECTION = 'SpeedrunTimes'   # rethawed.ini section holding the best times (one key per level, value = ms). Evan 2026-10-16: ini, not the skater profile.

def ini_key_script():
    # rt_speedrun_ini_key level=<Load_Z_..> -> RETURN rt_key = "l<hex>" and rt_kok = 1 (rt_kok = 0 for a level no tour lists)
    from qbasm import asm, S, NL, IF, ENDIF, ENDSCRIPT, RETURN
    body = [NL, 'rt_kok', '=', 0, NL, 'rt_key', '=', S('none'), NL]
    for c in sorted({c for t in tour_facts() for c in t['levels']}):
        body += [IF, 'ChecksumEquals', 'a', '=', '<level', 'b', '=', '$%08x' % c, NL,
                 'rt_kok', '=', 1, NL, 'rt_key', '=', S('l%08x' % c), NL, ENDIF, NL]
    return asm(body + [RETURN, 'rt_kok', '=', '<rt_kok', 'rt_key', '=', '<rt_key', NL, ENDSCRIPT])

def get_ms_script():
    # rt_speedrun_get_ms level=<Load_Z_..> -> RETURN rt_ms = best time in ms, or -1 if none. Reads the ini. If the ini has nothing, falls back to the old
    # skater-profile list (rt_sr_times) once and copies a found time into the ini (lazy migration, per skater the player picks).
    from qbasm import asm, S, NL, NOT, BEGIN, REPEAT, ENDSCRIPT, ENDIF, IF, ELSE, RETURN
    return asm([NL,
        'rt_ms', '=', -1, NL,
        'rt_speedrun_ini_key', 'level', '=', '<level', NL,
        IF, '(', '<rt_kok', '=', 1, ')', NL,
            'RT_GetINIValue', 'SECTION', '=', S(INI_SECTION), 'key', '=', '<rt_key', 'default', '=', 0, NL,
            IF, '(', '<value', '>', 0, ')', NL,
                'rt_ms', '=', '<value', NL,
            ELSE, NL,
                'GetCurrentSkaterProfileIndex', NL, 'GetSkaterProfileInfo', 'player', '=', '<$3c64476b', NL,
                IF, 'GotParam', 'rt_sr_times', NL,
                    'GetArraySize', '<rt_sr_times', NL, 'rt_i', '=', 0, NL,
                    IF, '(', '<array_size', '>', 0, ')', NL,
                    BEGIN, NL,
                        IF, 'ChecksumEquals', 'a', '=', '(', '(', '<rt_sr_times', '[', '<rt_i', ']', ')', '.', 'level', ')', 'b', '=', '<level', NL,
                            'rt_ms', '=', '(', '(', '<rt_sr_times', '[', '<rt_i', ']', ')', '.', 'ms', ')', NL, ENDIF, NL,
                        'rt_i', '=', '(', '<rt_i', '+', 1, ')', NL,
                    REPEAT, '<array_size', NL,
                    ENDIF, NL,
                ENDIF, NL,
                IF, '(', '<rt_ms', '>', 0, ')', NL,
                    'RT_SetINIValue', 'SECTION', '=', S(INI_SECTION), 'key', '=', '<rt_key', 'value', '=', '<rt_ms', NL,
                    'Printf', S('RTSR best migrated to ini %a ms'), 'a', '=', '<rt_ms', NL,
                ELSE, NL, 'rt_ms', '=', -1, NL, ENDIF, NL,
            ENDIF, NL,
        ENDIF, NL,
        RETURN, 'rt_ms', '=', '<rt_ms', NL, ENDSCRIPT])

def save_best_script():
    # rt_speedrun_save_best: best time per level lives in rethawed.ini [SpeedrunTimes] l<hex of Load_Z_..> = ms (same for every skater)
    # RETURNs rt_newbest = 1 when this run set the level's first or a lower time
    from qbasm import asm, S, NL, NOT, BEGIN, REPEAT, ENDSCRIPT, ENDIF, IF, ELSE, RETURN
    return asm([NL,
        'GetCurrentLevel', NL,
        'rt_new', '=', '(', 'rt_sr_ms', ')', NL, 'rt_newbest', '=', 0, NL,
        'rt_speedrun_get_ms', 'level', '=', '<level', NL,
        'rt_speedrun_ini_key', 'level', '=', '<level', NL,
        IF, '(', '<rt_kok', '=', 1, ')', NL,
            IF, '(', '<rt_ms', '<', 0, ')', NL,
                'RT_SetINIValue', 'SECTION', '=', S(INI_SECTION), 'key', '=', '<rt_key', 'value', '=', '<rt_new', NL,
                'Printf', S('RTSR best new %a ms'), 'a', '=', '<rt_new', NL, 'rt_newbest', '=', 1, NL,
            ELSE, NL,
                IF, '(', '<rt_new', '<', '<rt_ms', ')', NL,
                    'RT_SetINIValue', 'SECTION', '=', S(INI_SECTION), 'key', '=', '<rt_key', 'value', '=', '<rt_new', NL,
                    'Printf', S('RTSR best improved %a ms'), 'a', '=', '<rt_new', NL, 'rt_newbest', '=', 1, NL,
                ELSE, NL,
                    'Printf', S('RTSR best kept %a ms'), 'a', '=', '<rt_ms', NL,
                ENDIF, NL,
            ENDIF, NL,
            'RT_GetINIValue', 'SECTION', '=', S(INI_SECTION), 'key', '=', '<rt_key', 'default', '=', 0, NL,
            'Printf', S('RTSR best ini read back %a'), 'a', '=', '<value', NL,
        ELSE, NL, 'Printf', S('RTSR best: level has no ini key'), NL, ENDIF, NL,
        RETURN, 'rt_newbest', '=', '<rt_newbest', NL, ENDSCRIPT])

def save_best_script_profile_old():
    # OLD (until v42): skater-profile storage. Kept for reference only, no longer registered.
    from qbasm import asm, S, NL, NOT, BEGIN, REPEAT, ENDSCRIPT, ENDIF, IF, ELSE, RETURN
    P_ = '$3c64476b'      # GetCurrentSkaterProfileIndex result
    entry = ['{', 'level', '=', '<level', 'ms', '=', '<rt_new', '}']
    return asm([NL,
        'GetCurrentLevel', NL, 'GetCurrentSkaterProfileIndex', NL,
        'GetSkaterProfileInfo', 'player', '=', '<' + P_, NL,
        IF, NOT, 'GotParam', 'rt_sr_times', NL, 'rt_sr_times', '=', '[', ']', NL, ENDIF, NL,
        'rt_new', '=', '(', 'rt_sr_ms', ')', NL, 'rt_found', '=', -1, NL, 'rt_i', '=', 0, NL, 'rt_newbest', '=', 0, NL,
        'GetArraySize', '<rt_sr_times', NL,
        IF, '(', '<array_size', '>', 0, ')', NL,
            BEGIN, NL,
                IF, 'ChecksumEquals', 'a', '=', '(', '(', '<rt_sr_times', '[', '<rt_i', ']', ')', '.', 'level', ')', 'b', '=', '<level', NL,
                    'rt_found', '=', '<rt_i', NL, ENDIF, NL,
                'rt_i', '=', '(', '<rt_i', '+', 1, ')', NL,
            REPEAT, '<array_size', NL,
        ENDIF, NL,
        IF, '(', '<rt_found', '=', -1, ')', NL,
            'AddArrayElement', 'array', '=', '<rt_sr_times', 'element', '=', ] + entry + [NL,
            'rt_sr_times', '=', '<array', NL,
            'Printf', S('RTSR best new %a ms'), 'a', '=', '<rt_new', NL, 'rt_newbest', '=', 1, NL,
        ELSE, NL,
            'rt_old', '=', '(', '(', '<rt_sr_times', '[', '<rt_found', ']', ')', '.', 'ms', ')', NL,
            IF, '(', '<rt_new', '<', '<rt_old', ')', NL,
                'SetArrayElement', '$8f61d602', '=', 'rt_sr_times', 'Index', '=', '<rt_found', '$4b40630d', '=', ] + entry + [NL,
                'Printf', S('RTSR best improved %a ms'), 'a', '=', '<rt_new', NL, 'rt_newbest', '=', 1, NL,
            ELSE, NL,
                'Printf', S('RTSR best kept %a ms'), 'a', '=', '<rt_old', NL,
            ENDIF, NL,
        ENDIF, NL,
        'SetSkaterProfileInfo', 'player', '=', '<' + P_, 'params', '=', '{', 'rt_sr_times', '=', '<rt_sr_times', '}', NL,
        # read back, to prove the profile kept it
        'RemoveParameter', 'rt_sr_times', NL, 'GetSkaterProfileInfo', 'player', '=', '<' + P_, NL,
        IF, 'GotParam', 'rt_sr_times', NL, 'GetArraySize', '<rt_sr_times', NL,
            'Printf', S('RTSR best entries on profile %a'), 'a', '=', '<array_size', NL,
        ELSE, NL, 'Printf', S('RTSR best NOT on profile'), NL, ENDIF, NL,
        RETURN, 'rt_newbest', '=', '<rt_newbest', NL, ENDSCRIPT])

SHOW_BEST = bool(os.environ.get('SHOW_BEST'))   # level select: "BEST TIME: M:SS" / "BEST TIME: -" in place of Classic's cash/stats line

def best_text_script():
    # rt_speedrun_best_text level=<Load_Z_..>  ->  RETURN rt_bt = "BEST TIME: M:SS" or "BEST TIME: -"
    from qbasm import asm, S, NL, NOT, BEGIN, REPEAT, ENDSCRIPT, ENDIF, IF, ELSE, RETURN
    return asm([NL,
        'rt_lvl', '=', '<level', NL, 'rt_bt', '=', S('BEST TIME: -'), NL, 'rt_bm', '=', S('-'), NL,
        'rt_has', '=', 1 if os.environ.get('BADGE_TEST') else 0, NL,      # BADGE_TEST (test builds only): every level shows the badge
        'rt_speedrun_get_ms', 'level', '=', '<rt_lvl', NL,
        IF, '(', '<rt_ms', '>', -1, ')', NL,
            'rt_has', '=', 1, NL,
            'Mod', 'a', '=', '<rt_ms', 'b', '=', 1000, NL,
            'rt_sec', '=', '(', '(', '<rt_ms', '-', '<Mod', ')', '/', 1000, ')', NL, 'CastToInteger', 'rt_sec', NL,
            'Mod', 'a', '=', '<rt_sec', 'b', '=', 60, NL,
            'rt_min', '=', '(', '(', '<rt_sec', '-', '<Mod', ')', '/', 60, ')', NL, 'CastToInteger', 'rt_min', NL,
            'rt_sec', '=', '<Mod', NL,
            IF, '(', '<rt_sec', '<', 10, ')', NL,
                'FormatText', 'TextName', '=', 'rt_bm', S('%m:0%s'), 'm', '=', '<rt_min', 's', '=', '<rt_sec', NL,
            ELSE, NL,
                'FormatText', 'TextName', '=', 'rt_bm', S('%m:%s'), 'm', '=', '<rt_min', 's', '=', '<rt_sec', NL,
            ENDIF, NL,
            'FormatText', 'TextName', '=', 'rt_bt', S('BEST TIME: %a'), 'a', '=', '<rt_bm', NL,
        ENDIF, NL,
        RETURN, 'rt_bt', '=', '<rt_bt', 'rt_bm', '=', '<rt_bm', 'rt_has', '=', '<rt_has', NL, ENDSCRIPT])

def patch_levelinfo_best(d):
    # levelselectmenu $d8ac364b fills the level info panel (Classic: goals line $d7abd035, cash/stats line $aedd3136).
    # Appended at the end (top level): in speedrun, the cash/stats line shows the best time instead.
    from qbasm import asm, NL, IF, ENDIF
    p = next(p for p, t, n in items(d) if t == 7 and n == 0xd8ac364b)
    s = script_at(d, p); assert s.endswith(b'\x01\x24'), s[-4:].hex()
    from qbasm import ELSE
    polish = []
    if MENU_POLISH:
        # Classic leftovers (built by $c0d1d785 TYPE=Classic): cash/stats icon $41b584e9, goals icon $d1d6fd6c + goals line $d7abd035,
        # medal parts ($5530e5ac). The level badge ($add283b5) shows Classic completion; in speedrun it means "has a best time".
        polish = [x for e in ('$41b584e9', '$d1d6fd6c', '$d7abd035', '$118ad348', '$cab6b4fa', '$2c1487e6')
                  for x in ('SetScreenElementProps', 'id', '=', e, 'Hide', NL)] + [
                  # badge $c8cdd165 = grey board $8af5b1fb + overlay $8d56ab6f ("?" $63c293e0, rethawed_sprites). Speedrun swaps the "?" for
                  # the global pak's white checkmark (no stopwatch icon exists in the loaded textures; panelsprites "watch" is a crossed-out clock)
                  IF, '(', '<rt_has', '=', 1, ')', NL, '$add283b5', 'TYPE', '=', '$c8cdd165', NL,
                  'SetScreenElementProps', 'id', '=', '$8d56ab6f', 'TEXTURE', '=', 'checkmark', NL,
                  ELSE, NL, '$add283b5', 'TYPE', '=', '$694e8069', NL, ENDIF, NL,
                  # the panels rotate ($72820896 on $ef518820): Classic (best time) <-> info (HIGH SCORE / GAPS FOUND) <-> level base / remastered by.
                  # Drop the info panel and restart the rotation without it, as the script's own tail does.
                  IF, 'ScreenElementExists', 'id', '=', '$bc68f881', NL,
                      'TerminateObjectsScripts', 'id', '=', '$ef518820', NL, 'DestroyScreenElement', 'id', '=', '$bc68f881', NL,
                      'RunScriptOnScreenElement', 'id', '=', '$ef518820', '$72820896', NL, ENDIF, NL]
    add = asm(sr_wrap([IF, 'GotParam', 'level', NL, 'rt_speedrun_best_text', 'level', '=', '<level', NL,
                       'SetScreenElementProps', 'id', '=', '$aedd3136', 'text', '=', '<rt_bt', NL,
                       'DoScreenElementMorph', 'id', '=', '$aedd3136', 'alpha', '=', 1, NL] + polish + [ENDIF, NL]))
    d = add_script(d, h('rt_speedrun_best_text'), best_text_script())
    return relocate_script(d, 0xd8ac364b, s[:-1] + a4(add) + b'\x24', h('rt_old_level_info'))

def patch_success_hook(d):
    # goal_success ($e1d422b1, goal_utilities.qb): first statement after its default-param struct
    from qbasm import asm, NL, IF, ENDIF
    p = next(p for p, t, n in items(d) if t == 7 and n == 0xe1d422b1)
    s = script_at(d, p); i = s.index(b'\x04\x01') + 2; assert s[0] == 3
    # spawned, not called inline, so nothing in our check can disturb the goal's own success flow
    add = asm(sr_wrap(['SpawnScriptLater', 'rt_speedrun_goal_won', 'params', '=', '{', 'goal_id', '=', '<goal_id', '}', NL]))
    return relocate_script(d, 0xe1d422b1, s[:i] + a4(add) + s[i:], h('rt_old_goal_success'))

def patch_clock(d):
    # adds rt_speedrun_clock and hooks it into the TimedScoreRun activate / deactivate scripts (needs PACK=stable: the file grows)
    from qbasm import asm, NL, IF, ENDIF, S
    d = add_script(d, CLOCK_SCRIPT, clock_script())
    init = []; final = []
    if FINISH and SR_GLOBAL:
        d = add_global(d, h('rt_sr_done'), 'int', 0)
        d = add_global(d, h('rt_sr_ms'), 'int', 0)
        if SAVE_BEST:
            d = add_script(d, h('rt_speedrun_save_best'), save_best_script())
            d = add_script(d, h('rt_speedrun_ini_key'), ini_key_script())
            d = add_script(d, h('rt_speedrun_get_ms'), get_ms_script())
            if RUN_END:
                d = add_script(d, h('rt_speedrun_newbest'), newbest_script())
                d = add_global(d, h('rt_sr_nb'), 'int', 0)
                d = add_global(d, h('rt_sr_run'), 'int', 0)
                d = add_global(d, h('rt_sr_has'), 'int', 0)
                d = add_global(d, h('rt_sr_retry'), 'int', 0)
                d = add_script(d, h('rt_speedrun_fmt_ms'), fmt_ms_script())
        # $617093d4 = "level-complete already handled" flag of $ea893bed: clear it so every run can finish
        init = ['change', 'rt_sr_done', '=', 0, NL, 'change', '$617093d4', '=', 0, NL]
    elif FINISH:
        d = add_script(d, h('rt_speedrun_goal_won'), won_script())
        # globals must exist as items (v18: `change` on undefined names did nothing -> clock stuck at 0:00)
        d = add_global(d, h('rt_sr_done'), 'int', 0)
        d = add_global(d, h('rt_sr_goal'), 'checksum', 0)
        d = add_global(d, h('rt_sr_final'), 'string', '0:00')
        init = ['change', 'rt_sr_done', '=', 0, NL, 'change', 'rt_sr_goal', '=', '<goal_id', NL,
                'change', 'rt_sr_final', '=', S('0:00'), NL]
        # after the finish the run's goal is deactivated: put the final time back into the themed timer
        final = [IF, 'GameModeEquals', 'rt_speedrun', NL, IF, '(', 'rt_sr_done', '=', 1, ')', NL,
                 IF, 'ScreenElementExists', 'id', '=', 'the_time', NL,
                    'ResolveScreenElementID', 'id', '=', '{', 'the_time', 'child', '=', 'text', '}', NL,
                    IF, 'ScreenElementExists', 'id', '=', '<$28e66b14', NL,
                        'SetScreenElementProps', 'id', '=', '<$28e66b14', 'text', '=', '(', 'rt_sr_final', ')', NL, ENDIF, NL,
                 ENDIF, NL, ENDIF, NL, ENDIF, NL]
    if SR_LOG:
        init = ['Printf', S('RTSR run start'), NL] + init
    if PARK_TOUR:
        # Classic records a goal win into the CURRENT tour at win time (UnBeatGoal doesn't undo it). During a speedrun run another
        # tour is made current ("parked"), so wins aren't recorded; the real tour comes back at the finish / when the run's goal ends.
        from qbasm import ELSE
        d = add_global(d, h('rt_sr_tour'), 'checksum', 0)
        d = add_global(d, h('rt_sr_parked'), 'int', 0)
        cache = []
        init = init + current_tour_tokens('rt_cur_t') + [
            IF, '(', 'rt_sr_parked', '=', 0, ')', NL] + cache + [
                'change', 'rt_sr_tour', '=', '<rt_cur_t', NL, 'change', 'rt_sr_parked', '=', 1, NL,
                IF, 'ChecksumEquals', 'a', '=', '<rt_cur_t', 'b', '=', 'THAW', NL, 'RTCM_ChooseTour', 'thps2', NL,
                ELSE, NL, 'RTCM_ChooseTour', 'THAW', NL, ENDIF, NL,
                'Printf', S('RTSR tour parked (real %g)'), 'g', '=', '<rt_cur_t', NL,
                'GetClassicLevelGoalCount', NL, 'Printf', S('RTSR decoy tour progress at park %a'), 'a', '=', '<$b35f8a59', NL,
            ENDIF, NL]
        final = final + unpark_tokens()
    if AUTOWIN:
        d = add_script(d, h('rt_speedrun_autowin'), autowin_script())
        init = init + ['KillSpawnedScript', 'name', '=', 'rt_speedrun_autowin', NL, 'SpawnScriptLater', 'rt_speedrun_autowin', NL]
    start = asm(sr_wrap(init + ['KillSpawnedScript', 'name', '=', 'rt_speedrun_clock', NL, 'SpawnScriptLater', 'rt_speedrun_clock', NL]))
    stop = asm((['Printf', S('RTSR run end'), NL] if SR_LOG and SR_GLOBAL else []) +
               (['KillSpawnedScript', 'name', '=', 'rt_speedrun_autowin', NL] if AUTOWIN else []) +
               ['KillSpawnedScript', 'name', '=', 'rt_speedrun_clock', NL,
                IF, 'ScreenElementExists', 'id', '=', 'rt_sr_clock', NL, 'DestroyScreenElement', 'id', '=', 'rt_sr_clock', NL, ENDIF, NL] + final)
    for sh, add, old in ((TSR_ACTIVATE, start, 'rt_old_tsr_activate'), (TSR_DEACTIVATE, stop, 'rt_old_tsr_deactivate')):
        p = next(p for p, t, n in items(d) if t == 7 and n == sh)
        s = script_at(d, p); assert s[0] == 1
        d = relocate_script(d, sh, s[:1] + a4(add) + s[1:], h(old))
    return d

MENU_ENTRY = bool(os.environ.get('MENU_ENTRY'))   # main menu gets its own SPEEDRUN MODE item (Classic stays Classic)
MM_BUILDER = 0x8fcbea04        # z_mainmenu_scripts.qb (inside data/mod/Zones/z_mainmenu/z_mainmenu.pak.wpc): builds the main menu list
MM_PAK_INDEX = 385             # that qb's entry in the zone pak

def patch_menu_entry_helpers(d):
    # gamemenu_helpers.qb: the global switch and the new item's select script (Classic's own item calls $f12cf4a0 <...>)
    from qbasm import asm, NL, ENDSCRIPT, PASS, IF, ENDIF
    d = add_global(d, h('rt_speedrun_on'), 'int', 1 if SR_DEFAULT_ON else 0)
    # Free Skate's own full-stats logic: its menu script (0546ca93) ends with `$5175c1d2 ON` = RTCM_SetClassicStatOverride 10 +
    # UpdateSkaterStats (skater select then shows 10s and no stat editing). Classic's menu script 6d344afd ends with `$5175c1d2 Off`;
    # in speedrun, switch it back ON right after (appended at the end, so nothing before it moves).
    p = next(p for p, t, n in items(d) if t == 7 and n == 0x6d344afd)
    s = script_at(d, p); assert s.endswith(b'\x01\x24')
    d = relocate_script(d, 0x6d344afd, s[:-1] + a4(asm([IF, '(', 'rt_speedrun_on', '=', 1, ')', NL, '$5175c1d2', 'ON', NL, ENDIF, NL])) + b'\x24',
                        h('rt_old_classic_menu'))
    if FLAG_PROBE: d = add_script(d, h('rt_speedrun_flag_probe'), flag_probe_script())
    return add_script(d, h('rt_speedrun_menu_select'),
                      asm([NL, 'change', 'rt_speedrun_on', '=', 1, NL] + (['rt_speedrun_flag_probe', NL] if FLAG_PROBE else []) + ['$f12cf4a0', PASS, NL, ENDSCRIPT]))

FLAG_PROBE = bool(os.environ.get('FLAG_PROBE'))   # TEST ONLY: can RTCM persistent flags carry a 24-bit time, and do they survive a skater save + restart?

def flag_probe_script():
    # TEST ONLY. RTCM persistent flags can't hold arbitrary keys (checked: a flag set at the SPEEDRUN MODE entry reads back false in the same
    # session, plain / TOUR=All / TOUR=thps2). Probe the mod's own ini natives instead (used by ReThawed's options): each run prints the stored
    # counter, then stores counter+1 and a 32-bit value keyed by a level hash. Read it again after a restart / skater switch.
    from qbasm import asm, S, NL, ENDSCRIPT
    return asm([NL,
        'RT_GetINIValue', 'SECTION', '=', S('SpeedrunProbe'), 'key', '=', S('counter'), 'default', '=', 0, NL,
        'Printf', S('RTSR INI counter read %a'), 'a', '=', '<value', NL,
        'rt_n', '=', '(', '<value', '+', 1, ')', NL,
        'RT_SetINIValue', 'SECTION', '=', S('SpeedrunProbe'), 'key', '=', S('counter'), 'value', '=', '<rt_n', NL,
        'RT_SetINIValue', 'SECTION', '=', S('SpeedrunProbe'), 'key', '=', S('big'), 'value', '=', 1193046123, NL,
        'RT_GetINIValue', 'SECTION', '=', S('SpeedrunProbe'), 'key', '=', S('big'), 'default', '=', 0, NL,
        'Printf', S('RTSR INI big read back %a (wrote 1193046123)'), 'a', '=', '<value', NL,
        ENDSCRIPT])

def patch_mainmenu_builder(d):
    # In the main menu builder: switch speedrun off whenever the main menu is built, and add SPEEDRUN MODE right after CLASSIC MODE
    # (inserted at top level just before `Localize $0c0dc2c4`, the Create-a-Modes item).
    from qbasm import asm, S, P, NL
    p = next(p for p, t, n in items(d) if t == 7 and n == MM_BUILDER)
    s = script_at(d, p); assert s[0] == 1
    at = b'\x16' + struct.pack('<I', h('Localize')) + b'\x16' + struct.pack('<I', 0x0c0dc2c4)
    assert s.count(at) == 1
    i = s.index(at); assert s[i-1] == 1
    item = asm(['$d86f02ac', '{', NL,
        'text', '=', S('SPEEDRUN MODE'), NL,
        'ICON', '=', '$6411ff2a', NL,
        'rot', '=', 0, NL, '$fa0bdff1', '=', 30, NL, '$41df069e', '=', P(1.0, 0.0), NL, '$2660cd26', '=', '$5eb7e47c', NL,
        '$b52f44ef', '=', '{', 'text', '=', S('Race the clock through every goal of a classic level.'), '}', NL,
        '$2b0cfca1', '=', '$830f2c1d', NL,
        '$b8437568', '=', '{', NL, '$b3f15167', NL, '$a2369165', '=', 'rt_speedrun_menu_select', NL, '}', NL,
        '}', NL, NL])
    off = asm(['change', 'rt_speedrun_on', '=', 0, NL] )
    s2 = s[:1] + a4(off) + s[1:i] + a4(item) + s[i:]
    return relocate_script(d, MM_BUILDER, s2, h('rt_old_mainmenu_builder'))

def build_mainmenu_pak(src_pak, out_pak):
    sys.path.insert(0, here)
    import zonepak
    pak = open(src_pak, 'rb').read()
    e = zonepak.entries(pak)[MM_PAK_INDEX]
    assert e['fname'].endswith('z_mainmenu_scripts.qb.wpc'), e['fname']
    qb = patch_mainmenu_builder(pak[e['abs']:e['abs']+e['size']])
    open(out_pak, 'wb').write(zonepak.replace_file(pak, MM_PAK_INDEX, qb))
    print('z_mainmenu.pak.wpc', len(pak), '->', os.path.getsize(out_pak))

TOUR_SWITCH = bool(os.environ.get('TOUR_SWITCH'))   # speedrun level select: left/right cycles the Classic tours
LS_BUILD = 0x94559c4d          # levelselectmenu: builds the level list (Classic: RTCM_GetTourLevelListName) and sets the pad handlers

def tour_cycle_script():
    # rt_speedrun_tour_cycle [left]: choose the previous/next tour of RTCM_GetTourList, then rebuild the list like $da31dfd3 does
    from qbasm import asm, NL, NOT, BEGIN, REPEAT, ENDSCRIPT, ENDIF, IF, ELSE, RETURN
    TL = '$9a07e7c7'
    return asm([NL,
        'RTCM_GetTourList', NL, 'GetArraySize', '<' + TL, NL,
        IF, '(', '<array_size', '<', 1, ')', NL, RETURN, NL, ENDIF, NL,
        'rt_ti', '=', 0, NL, 'rt_cur', '=', 0, NL,
        BEGIN, NL,
            'rt_t', '=', '(', '(', '<' + TL, '[', '<rt_ti', ']', ')', '.', 'id', ')', NL,
            IF, 'RTCM_TourIs', '<rt_t', NL, 'rt_cur', '=', '<rt_ti', NL, ENDIF, NL,
            'rt_ti', '=', '(', '<rt_ti', '+', 1, ')', NL,
        REPEAT, '<array_size', NL,
        IF, 'GotParam', 'left', NL,
            'rt_cur', '=', '(', '<rt_cur', '-', 1, ')', NL,
            IF, '(', '<rt_cur', '<', 0, ')', NL, 'rt_cur', '=', '(', '<array_size', '-', 1, ')', NL, ENDIF, NL,
        ELSE, NL,
            'rt_cur', '=', '(', '<rt_cur', '+', 1, ')', NL,
            IF, '(', '<rt_cur', '=', '<array_size', ')', NL, 'rt_cur', '=', 0, NL, ENDIF, NL,
        ENDIF, NL,
        'rt_tour', '=', '(', '(', '<' + TL, '[', '<rt_cur', ']', ')', '.', 'id', ')', NL] + (
        # arrow bump on the tour bar, like Free Skate's category arrows ($99ffbca3)
        [IF, 'GotParam', 'left', NL, '$99ffbca3', 'left', NL, ELSE, NL, '$99ffbca3', 'Right', NL, ENDIF, NL] if MENU_POLISH else []) + [
        'RTCM_ChooseTour', '<rt_tour', NL,
        'Printf', S_('RTSR tour %g'), 'g', '=', '<rt_tour', NL,
        '$ea2791f5', NL, '$94559c4d', NL,
        ENDSCRIPT])

def S_(t):
    from qbasm import S
    return S(t)

MENU_POLISH = bool(os.environ.get('MENU_POLISH'))   # speedrun menus: tour bar + hints in level select, no Classic leftovers, run menu cleanup
TOUR_NAMES = (('thps2', "TONY HAWK'S PRO SKATER 2"), ('thps3', "TONY HAWK'S PRO SKATER 3"), ('THUG2', "TONY HAWK'S UNDERGROUND 2"),
              ('THAW', "TONY HAWK'S AMERICAN WASTELAND"), ('THP8', "TONY HAWK'S PROJECT 8"))

def tour_label_script():
    # rt_speedrun_tour_label: Free Skate's page text ($cb15c39e, centred between the arrows) = "TOUR i/n", category text ($5517c62d) = tour name
    from qbasm import asm, S, NL, BEGIN, REPEAT, ENDSCRIPT, ENDIF, IF
    TL = '$9a07e7c7'
    names = []
    for k, v in TOUR_NAMES:
        names += [IF, 'ChecksumEquals', 'a', '=', '<rt_t', 'b', '=', k, NL, 'rt_tn', '=', S(v), NL, ENDIF, NL]
    return asm([NL,
        'RTCM_GetTourList', NL, 'GetArraySize', '<' + TL, NL,
        'rt_ti', '=', 0, NL, 'rt_cur', '=', 0, NL, 'rt_t', '=', 'none', NL, 'rt_tn', '=', S('CLASSIC TOURS'), NL,
        IF, '(', '<array_size', '>', 0, ')', NL,
        BEGIN, NL,
            'rt_x', '=', '(', '(', '<' + TL, '[', '<rt_ti', ']', ')', '.', 'id', ')', NL,
            IF, 'RTCM_TourIs', '<rt_x', NL, 'rt_cur', '=', '<rt_ti', NL, 'rt_t', '=', '<rt_x', NL, ENDIF, NL,
            'rt_ti', '=', '(', '<rt_ti', '+', 1, ')', NL,
        REPEAT, '<array_size', NL,
        ENDIF, NL] + names + [
        'FormatText', 'TextName', '=', 'rt_tp', S('TOUR %a/%b'), 'a', '=', '(', '<rt_cur', '+', 1, ')', 'b', '=', '<array_size', NL,
        'SetScreenElementProps', 'id', '=', '$cb15c39e', 'text', '=', '<rt_tp', NL,
        'SetScreenElementProps', 'id', '=', '$5517c62d', 'text', '=', '<rt_tn', NL,
        ENDSCRIPT])

COMP_GREY = bool(os.environ.get('COMP_GREY'))     # speedrun level select: competition levels greyed out and not selectable
TOUR_TOTAL = bool(os.environ.get('TOUR_TOTAL'))   # speedrun tour select: TOTAL TIME (sum of best times, once every goal level has one)
_TOURS = None
def tour_facts():
    global _TOURS
    if _TOURS is None:
        import tourdata
        _TOURS = tourdata.tours(FILES)
    return _TOURS

def is_comp_script():
    # rt_speedrun_is_comp level=<Load_Z_..> -> RETURN rt_comp = 1 for competition levels (from classic_tours.qb at build time)
    from qbasm import asm, NL, IF, ENDIF, ENDSCRIPT, RETURN
    body = [NL, 'rt_comp', '=', 0, NL]
    for c in sorted({c for t in tour_facts() for c in t['comps']}):
        body += [IF, 'ChecksumEquals', 'a', '=', '<level', 'b', '=', '$%08x' % c, NL, 'rt_comp', '=', 1, NL, ENDIF, NL]
    return asm(body + [RETURN, 'rt_comp', '=', '<rt_comp', NL, ENDSCRIPT])

def pad2(dst, src, fmt_lt10, fmt):
    from qbasm import S, NL, IF, ELSE, ENDIF
    return [IF, '(', '<' + src, '<', 10, ')', NL, 'FormatText', 'TextName', '=', dst, S(fmt_lt10), 'a', '=', '<' + src, NL,
            ELSE, NL, 'FormatText', 'TextName', '=', dst, S(fmt), 'a', '=', '<' + src, NL, ENDIF, NL]

def tour_total_script():
    # rt_speedrun_tour_total tour=<tour id> -> RETURN rt_tt = "TOTAL TIME: [H:]MM:SS" (or "-:--" until every goal level of the tour has a best
    # time on the skater profile) and rt_lt = "LEVELS DONE: a / b". Competition levels don't count.
    from qbasm import asm, S, NL, IF, ELSE, ENDIF, ENDSCRIPT, RETURN, BEGIN, REPEAT
    pick = []
    for t in tour_facts():
        if t['id'] is None: continue
        lv = [x for x in t['levels'] if x not in t['comps']]
        pick += [IF, 'ChecksumEquals', 'a', '=', '<tour', 'b', '=', '$%08x' % t['id'], NL,
                 'rt_lv', '=', '['] + ['$%08x' % x for x in lv] + [']', NL, ENDIF, NL]
    find = [  # rt_ms = best time of <rt_l> or -1 (TOTAL_TEST, test builds only: every level counts as 12:20.5)
        'rt_speedrun_get_ms', 'level', '=', '<rt_l', NL] + (['rt_ms', '=', 740500, NL] if os.environ.get('TOTAL_TEST') else [])
    return asm([NL,
        'rt_lv', '=', '[', ']', NL] + pick + [
        'GetCurrentSkaterProfileIndex', NL, 'GetSkaterProfileInfo', 'player', '=', '<$3c64476b', NL,
        'rt_sum', '=', 0, NL, 'rt_done', '=', 0, NL, 'rt_k', '=', 0, NL,
        'GetArraySize', '<rt_lv', NL, 'rt_n', '=', '<array_size', NL,
        IF, '(', '<rt_n', '>', 0, ')', NL, BEGIN, NL,
            'rt_l', '=', '(', '<rt_lv', '[', '<rt_k', ']', ')', NL] + find + [
            IF, '(', '<rt_ms', '>', -1, ')', NL, 'rt_sum', '=', '(', '<rt_sum', '+', '<rt_ms', ')', NL, 'rt_done', '=', '(', '<rt_done', '+', 1, ')', NL, ENDIF, NL,
            'rt_k', '=', '(', '<rt_k', '+', 1, ')', NL,
        REPEAT, '<rt_n', NL, ENDIF, NL,
        'FormatText', 'TextName', '=', 'rt_lt', S('LEVELS DONE: %a / %b'), 'a', '=', '<rt_done', 'b', '=', '<rt_n', NL,
        'rt_tt', '=', S('TOTAL TIME: -:--'), NL,
        IF, '(', '<rt_n', '>', 0, ')', NL, IF, '(', '<rt_done', '=', '<rt_n', ')', NL,
            'Mod', 'a', '=', '<rt_sum', 'b', '=', 1000, NL,
            'rt_s', '=', '(', '(', '<rt_sum', '-', '<Mod', ')', '/', 1000, ')', NL, 'CastToInteger', 'rt_s', NL,
            'Mod', 'a', '=', '<rt_s', 'b', '=', 60, NL,
            'rt_m', '=', '(', '(', '<rt_s', '-', '<Mod', ')', '/', 60, ')', NL, 'CastToInteger', 'rt_m', NL, 'rt_s', '=', '<Mod', NL,
            'Mod', 'a', '=', '<rt_m', 'b', '=', 60, NL,
            'rt_h', '=', '(', '(', '<rt_m', '-', '<Mod', ')', '/', 60, ')', NL, 'CastToInteger', 'rt_h', NL] +
            pad2('rt_sp', 'rt_s', '0%a', '%a') + [
            IF, '(', '<rt_h', '>', 0, ')', NL,
                'rt_m', '=', '<Mod', NL] + pad2('rt_mp', 'rt_m', '0%a', '%a') + [
                'FormatText', 'TextName', '=', 'rt_tt', S('TOTAL TIME: %h:%m:%s'), 'h', '=', '<rt_h', 'm', '=', '<rt_mp', 's', '=', '<rt_sp', NL,
            ELSE, NL,
                'FormatText', 'TextName', '=', 'rt_tt', S('TOTAL TIME: %m:%s'), 'm', '=', '<rt_m', 's', '=', '<rt_sp', NL,
            ENDIF, NL,
        ENDIF, NL, ENDIF, NL,
        RETURN, 'rt_tt', '=', '<rt_tt', 'rt_lt', '=', '<rt_lt', NL, ENDSCRIPT])

def patch_level_items_comp(d):
    # levelselectmenu $780f8810 builds one level item. Items with $4e0d7c69 = 0 are made greyed out and not focusable
    # (`IF ( <$4e0d7c69 = 0 )` -> { $1d944426 }, rgba 16/16/16/32). In speedrun, competitions get that too (inserted just before that IF).
    from qbasm import asm, NL, IF, ENDIF
    from qbsplice import splice
    p = next(p for p, t, n in items(d) if t == 7 and n == 0x780f8810)
    s = script_at(d, p)
    cond = b'\x0e\x2d\x16' + struct.pack('<I', 0x4e0d7c69) + b'\x07\x17' + struct.pack('<i', 0) + b'\x0f'
    j = s.index(cond)       # first of two such IFs: the one that sets $70422e55
    assert s[j+len(cond):j+len(cond)+6] == b'\x01\x16' + struct.pack('<I', 0x70422e55)
    i = j - 3; assert s[i] == 0x47 and s[i-1] == 1
    add = asm(sr_wrap([IF, 'GotParam', 'level', NL, 'rt_speedrun_is_comp', 'level', '=', '<level', NL,
                       IF, '(', '<rt_comp', '=', 1, ')', NL, '$4e0d7c69', '=', 0, NL, ENDIF, NL, ENDIF, NL]))
    d = add_script(d, h('rt_speedrun_is_comp'), is_comp_script())
    return relocate_script(d, 0x780f8810, splice(s, i, i, a4(add)), h('rt_old_level_item'))

def patch_tour_card(d):
    # rt_tour_select $d0d8ce3d builds the highlighted tour's card; a 2-pass loop (SWITCH <$26ba26d1) fills its two stat rows
    # ($85183078 text, $5982eb6d alpha): goals completed / gold medals. In speedrun: TOTAL TIME / LEVELS DONE.
    # Game mode is not Classic yet at tour select, so only rt_speedrun_on is tested.
    from qbasm import asm, S, NL, IF, ELSE, ENDIF
    from qbsplice import splice, tokens
    p = next(p for p, t, n in items(d) if t == 7 and n == 0xd0d8ce3d)
    s = script_at(d, p)
    on = lambda body: [IF, '(', 'rt_speedrun_on', '=', 1, ')', NL] + body + [ENDIF, NL]
    # 2. rows: right after the SWITCH's ENDSWITCH (inside the loop)
    sw = b'\x3c\x2d\x16' + struct.pack('<I', 0x26ba26d1)
    j = s.index(sw); assert s.count(sw) == 1
    e = next(q for q, t in tokens(s) if q > j and t == 0x3d)
    rows = asm([NL] + on([IF, '(', '<$26ba26d1', '=', 0, ')', NL, '$85183078', '=', '<rt_tt', NL, ELSE, NL, '$85183078', '=', '<rt_lt', NL, ENDIF, NL,
                          '$5982eb6d', '=', 1, NL]))
    s = splice(s, e + 1, e + 1, a4(rows))
    # 1. totals: right after the default-param struct
    assert s[0] == 3; i = s.index(b'\x04\x01') + 2
    tot = asm(on(['rt_tt', '=', S('TOTAL TIME: -:--'), NL, 'rt_lt', '=', S(''), NL, IF, 'ScreenElementExists', 'id', '=', '$21625c8b', NL, '$cb85030f', NL,
                  'rt_speedrun_tour_total', 'tour', '=', '(', '<$70e3cf3b', '.', 'id', ')', NL, ENDIF, NL]))
    s = splice(s, i, i, a4(tot))
    d = add_script(d, h('rt_speedrun_tour_total'), tour_total_script())
    return relocate_script(d, 0xd0d8ce3d, s, h('rt_old_tour_card'))

COLLECT_RESET = bool(os.environ.get('COLLECT_RESET'))   # speedrun run start: uninit/init collect goals so Retry after a won run respawns pickups
RUN_END = bool(os.environ.get('RUN_END'))   # session 3b: no TOUR STATS / RESTART TOUR, no record screen, NEW BEST TIME!!, pause best time

def wrap_stmt_off(s, i, j):
    """s[i:j] (whole statements) -> IF ( rt_speedrun_on = 0 ) <stmts> ENDIF, padded with newlines to a multiple of 4"""
    from qbasm import asm, NL
    from qbsplice import splice
    cond = asm(['(', 'rt_speedrun_on', '=', 0, ')', NL])
    body = s[i:j] + b'\x01'
    blk = b'\x47' + struct.pack('<H', 2 + len(cond) + len(body) + 1) + cond + body + b'\x28'
    blk += b'\x01' * ((-(len(blk) - (j - i))) % 4)
    return splice(s, i, j, blk)

def stmt_with_string(s, text, call):
    """(start, end) of the `$name { ... }` statement whose struct holds the string `text`"""
    from qbsplice import tokens
    k = s.index(b'\x1b' + struct.pack('<I', len(text) + 1) + text.encode('latin1') + b'\0')
    toks = tokens(s)
    st = s.rindex(b'\x16' + struct.pack('<I', call), 0, k); assert s[st-1] == 1     # the call that opens this struct
    depth = 0
    for p, t in toks:
        if p < st: continue
        if t == 3: depth += 1
        elif t == 4:
            depth -= 1
            if depth == 0: return st, p + 1
    raise ValueError(text)

def patch_tour_options(d):
    # rt_tour_select $7c46283e (TOUR OPTIONS after picking a tour): in speedrun hide TOUR STATS (Evan) and RESTART TOUR (it resets the
    # Classic tour's progress). Each is one `$56e68973 { text = ... }` statement; RESTART sits inside IF tour-started.
    p = next(p for p, t, n in items(d) if t == 7 and n == 0x7c46283e)
    s = script_at(d, p)
    for name in ('TOUR STATS', 'RESTART TOUR'):
        i, j = stmt_with_string(s, name, 0x56e68973)
        assert s[i:i+5] == b'\x16' + struct.pack('<I', 0x56e68973), s[i:i+5].hex()
        s = wrap_stmt_off(s, i, j)
    return relocate_script(d, 0x7c46283e, s, h('rt_old_tour_options'))

def patch_record_screen(d):
    # classic_mode_menu $b986e513 (after the end-of-run goals screen): UpdateRecords, then IF NewRecord -> $afee0e5f (high score /
    # initials screen) ELSE $09b2606c. In speedrun always take the ELSE path; records still update.
    from qbasm import asm, NL, IF, ENDIF
    from qbsplice import splice
    p = next(p for p, t, n in items(d) if t == 7 and n == 0xb986e513)
    s = script_at(d, p)
    old = b'\x47' + s[s.index(b'\x47') + 1:s.index(b'\x47') + 3] + b'\x16' + struct.pack('<I', h('NewRecord')) + b'\x01'
    i = s.index(old); assert s.count(old) == 1 and s[i-1] == 1
    s = splice(s, i + 3, i + 8, b'\x0e\x2d\x16' + struct.pack('<I', h('rt_rec')) + b'\x07\x17' + struct.pack('<i', 1) + b'\x0f' + b'\x01' * 3)
    pre = asm(['rt_rec', '=', 0, NL, IF, 'NewRecord', NL, 'rt_rec', '=', 1, NL, ENDIF, NL] + sr_wrap(['rt_rec', '=', 0, NL]))
    s = splice(s, i, i, a4(pre))
    return relocate_script(d, 0xb986e513, s, h('rt_old_record_screen'))

def newbest_script():
    # rt_speedrun_newbest: the deck-unlock message's jingle ($1d27fd8f: StopStream goal sfx, respawn $80beb065) and a themed panel
    # message (create_panel_message + the trick-message style $d8c9233a). Shown with the post-run menu (Evan: after the goals page),
    # just above the centred BEST TIME line, which carries the time.
    from qbasm import asm, S, NL, ENDSCRIPT
    return asm([NL,
        'StopStream', '$5db793aa', NL, 'KillSpawnedScript', 'name', '=', '$80beb065', NL, 'SpawnScriptLater', '$80beb065', NL,
        'Printf', S('RTSR new best message'), NL,
        ENDSCRIPT])

def patch_pause_cash(d):
    # create_pause_menu: `IF GameModeEquals Classic / IF $84c65fe5 (tour has cash) / IF <in-level>` adds SPEND SOME CASH. No cash exists in
    # speedrun (CASH_OFF), so hide it: the outer condition becomes ( <rt_cash = 1 ), computed just before as Classic and not speedrun.
    # (Same trick as the SKATE SHOP item in the run menu.) The pause and post-run menus both come from this script.
    import re
    from qbasm import asm, NL, IF, ENDIF
    from qbsplice import splice
    p = next(p for p, t, n in items(d) if t == 7 and n == h('create_pause_menu'))
    s = script_at(d, p)
    GME = b'\x16' + struct.pack('<I', 0x20985922) + b'\x16' + struct.pack('<I', 0x5a2c589a)
    pat = re.escape(GME) + b'\x01+\x47..\x16' + re.escape(struct.pack('<I', 0x84c65fe5))     # 01+ : the 2026-11 SDK compiler emits blank lines
    ms = [m for m in re.finditer(pat, s, re.S) if b'SPEND SOME CASH' in s[m.start():m.start() + 300]]; assert len(ms) == 1, len(ms)
    i = ms[0].start() - 3; assert s[i] == 0x47 and s[i-1] == 1
    s = splice(s, i + 3, i + 3 + len(GME), b'\x0e\x2d\x16' + struct.pack('<I', h('rt_cash')) + b'\x07\x17' + struct.pack('<i', 1) + b'\x0f' + b'\x01' * (len(GME) - 13 + 0))
    pre = asm(['rt_cash', '=', 0, NL, IF, 'GameModeEquals', '$5a2c589a', NL, 'rt_cash', '=', 1, NL, ENDIF, NL] + sr_wrap(['rt_cash', '=', 0, NL]))
    s = splice(s, i, i, a4(pre))
    return relocate_script(d, h('create_pause_menu'), s, h('rt_old_create_pause_menu'))

def patch_pause_combo(d):
    # Evan (v45): on the in-run pause menu the "Best Combo - n" stat ($25478905, called by create_pause_menu) becomes "Best Time - M:SS"
    # (same text as the post-run menu's top-left line, in Best Combo's place). On the post-run menu $8b0789f0 has already drawn its own
    # Best Time line ($603179db), so there the script is left alone. Inserted at the script start; speedrun draws the line and RETURNs.
    from qbasm import asm, S, NL, IF, NOT, ENDIF, RETURN
    p = next(p for p, t, n in items(d) if t == 7 and n == 0x25478905)
    s = script_at(d, p); assert s[0] == 1
    add = asm(sr_wrap([IF, NOT, 'ScreenElementExists', 'id', '=', '$603179db', NL,
        'GetCurrentLevel', NL, 'rt_speedrun_best_text', 'level', '=', '<level', NL,
        'FormatText', 'TextName', '=', 'rt_bc', S('Best Time - %a'), 'a', '=', '<rt_bm', NL,
        'CreateScreenElement', '{', NL, 'TYPE', '=', '$5200dfb6', NL, 'parent', '=', '$4aa5f466', NL, 'id', '=', 'high_combo', NL,
            'text', '=', '<rt_bc', NL, 'Font', '=', 'Dialog', NL, 'Just', '=', '[', 'left', ',', 'Top', ']', NL,
            'Scale', '=', P_(0.8, 0.8), NL, 'Pos', '=', P_(80.0, 72.0), NL, 'rgba', '=', '[', 128, 128, 128, 78, ']', NL,
            '$57710f31', '=', 20, NL, '$ed344e1b', '=', 0, NL, '}', NL,
        'CreateScreenElement', '{', NL, 'TYPE', '=', 'SpriteElement', NL, 'parent', '=', '$4aa5f466', NL, 'id', '=', '$24aece7c', NL,
            'TEXTURE', '=', '$0736e962', NL, 'rgba', '=', '[', 0, 0, 0, 100, ']', NL, 'Just', '=', '[', 'Right', ',', 'Top', ']', NL,
            'Pos', '=', P_(400.0, 72.0), NL, 'Scale', '=', P_(30.0, 4.0), NL, '$57710f31', '=', 19, NL, '$ed344e1b', '=', 0, NL, '}', NL,
        RETURN, NL, ENDIF, NL]))
    return relocate_script(d, 0x25478905, s[:1] + a4(add) + s[1:], h('rt_old_pause_combo'))

def patch_pause_save(d):
    # create_pause_menu: the stock Classic pause menu has no save item (saving is only offered by QUIT's "Save Progress?" prompt, $f9521440).
    # Evan: best times live on the skater profile, so speedrun gets SAVE PROGRESS just before YOUR PROGRESS. Its select script is the prompt's own
    # Yes handler ($d7f58e2e in memcard.qb: sets the memcard callbacks, returns to create_pause_menu when done, then saves).
    from qbasm import asm, S, NL
    from qbsplice import splice
    p = next(p for p, t, n in items(d) if t == 7 and n == h('create_pause_menu'))
    s = script_at(d, p)
    cond = b'\x0e\x2d\x16' + struct.pack('<I', 0x8530ace1) + b'\x07\x17' + struct.pack('<i', 1) + b'\x0f'
    assert s.count(cond) == 1
    i = s.index(cond) - 3; assert s[i] == 0x47 and s[i-1] == 1
    add = asm(sr_wrap(['$88e4e21c', '{', NL, 'text', '=', S('SAVE PROGRESS'), NL, 'tIP', '=', S('Save your skater profile, best times included.'), NL,
        'rot', '=', -2, NL, 'Scale', '=', 0.85, NL, 'id', '=', 'rt_sr_save_item', NL, '$2b0cfca1', '=', '$c859815a', NL,
        '$b8437568', '=', '{', NL, 'menu_select_script', '=', 'rt_speedrun_save', NL, '}', NL, '}', NL]))
    from qbasm import IF, ENDIF, ENDSCRIPT
    # the BEST TIME / NEW BEST / This run texts belong to the pause stats and would sit on top of the save list: remove them first
    # (create_pause_menu redraws the best time when the save screen returns to it)
    kill = []
    for e in ('rt_sr_best_center', 'rt_sr_newbest_text', 'rt_sr_thisrun'):
        kill += [IF, 'ScreenElementExists', 'id', '=', e, NL, 'DestroyScreenElement', 'id', '=', e, NL, ENDIF, NL]
    d = add_script(d, h('rt_speedrun_save'), asm([NL] + kill + ['$d7f58e2e', NL, ENDSCRIPT]))
    return relocate_script(d, h('create_pause_menu'), splice(s, i, i, a4(add)), h('rt_old_create_pause_menu'))

def fmt_ms_script():
    # rt_speedrun_fmt_ms ms=<n> -> RETURN rt_fm = "m:ss" (cut, like the clock)
    from qbasm import asm, S, NL, ENDSCRIPT, RETURN
    return asm([NL, 'Mod', 'a', '=', '<ms', 'b', '=', 1000, NL, 'rt_s', '=', '(', '(', '<ms', '-', '<Mod', ')', '/', 1000, ')', NL, 'CastToInteger', 'rt_s', NL,
        'Mod', 'a', '=', '<rt_s', 'b', '=', 60, NL, 'rt_m', '=', '(', '(', '<rt_s', '-', '<Mod', ')', '/', 60, ')', NL, 'CastToInteger', 'rt_m', NL, 'rt_s', '=', '<Mod', NL] +
        pad2('rt_sp', 'rt_s', '0%a', '%a') + ['FormatText', 'TextName', '=', 'rt_fm', S('%m:%s'), 'm', '=', '<rt_m', 's', '=', '<rt_sp', NL, RETURN, 'rt_fm', '=', '<rt_fm', NL, ENDSCRIPT])

def patch_levelload(d):
    # levels.qb $242fcb01 runs at every level load (before GoalManager_InitializeAllGoals): a level load is never a Retry, so clear rt_sr_retry
    from qbasm import asm, NL
    p = next(p for p, t, n in items(d) if t == 7 and n == 0x242fcb01)
    s = script_at(d, p); assert s[0] == 1
    return relocate_script(d, 0x242fcb01, s[:1] + a4(asm(['change', 'rt_sr_retry', '=', 0, NL])) + s[1:], h('rt_old_level_load'))

def patch_skip_goalspage(d):
    # classic_mode_menu $d2ab05d1 with $d9089e42 = the end-of-run goals page (Done -> $ea66b0cf -> $78e3a7f8 -> post-run menu).
    # In speedrun: do its end-of-run setup, then go straight to $78e3a7f8 (no page, so no extra press). Inserted before
    # `IF GotParam $d9089e42 / PauseGame`, so the THPS2-theme page ($40445a6d) is skipped too.
    from qbasm import asm, NL, IF, ENDIF, RETURN
    from qbsplice import splice
    p = next(p for p, t, n in items(d) if t == 7 and n == 0xd2ab05d1)
    s = script_at(d, p)
    at = b'\x16' + struct.pack('<I', h('GotParam')) + b'\x16' + struct.pack('<I', 0xd9089e42) + b'\x01'
    i = s.index(at) - 3; assert s[i] == 0x47 and s[i-1] == 1
    add = asm(sr_wrap([IF, 'GotParam', '$d9089e42', NL,
        'PauseGame', NL, '$fb03ab35', NL, '$5d102321', 'Off', NL,
        IF, 'ScreenElementExists', 'id', '=', 'current_menu_anchor', NL, 'DestroyScreenElement', 'id', '=', 'current_menu_anchor', NL, ENDIF, NL,
        '$07a68e6c', NL, '$29538a58', NL, '$05f35bcb', NL, '$dd814b9a', NL, '$fb03ab35', NL, 'ReplaceTrickText', 'All', NL,
        'SetScreenElementLock', 'id', '=', '$56a1eae3', 'Off', NL,
        'Printf', S_('RTSR goals page skipped'), NL,
        'SpawnScriptNow', '$78e3a7f8', NL, RETURN, NL, ENDIF, NL]))
    d = relocate_script(d, 0xd2ab05d1, splice(s, i, i, a4(add)), h('rt_old_goals_page'))
    # the end-of-run goal cameras ($397c3e70, when $e0c71c65 is true = not the THPS2 tour) are skipped as well
    p = next(p for p, t, n in items(d) if t == 7 and n == 0xe0c71c65)
    s = script_at(d, p); assert s[0] == 1
    return relocate_script(d, 0xe0c71c65, s[:1] + a4(asm(sr_wrap([RETURN, 'False', NL]))) + s[1:], h('rt_old_end_cams_check'))

def collect_reset_script():
    # rt_speedrun_reset_collect goal=<g>: untimed collect goals (goal_untimed_collect: init $13e9232a creates goal_collect_objects and
    # sets the total $5a76e075; pickups count up $6f291b42 and Die). A won run leaves the count full and the pickups gone, and Retry only
    # resets active goals, so: count = 0 and re-create every pickup that no longer exists (same call as init: $416fdf84).
    # Hangar's pilot wings are the other kind, goal_collect ($f959bf2b, init $a32233bb, THUG2 style: pickups CREATEd by node prefix in
    # init, killed on pickup; per-object goal flags when goal_collect_objects is used). Both kinds: zero the untimed count / the object
    # flags, then uninit (kills the prefix objects, uninit script $4e67452e) and init again (re-creates them), as goal_bike_goals does.
    from qbasm import asm, NL, IF, ENDIF, BEGIN, REPEAT, ENDSCRIPT
    return asm([NL,
        'GoalManager_GetGoalParams', 'name', '=', '<goal', NL, 'rt_ci', '=', 0, NL,
        IF, 'GotParam', 'init', NL,
            IF, 'ChecksumEquals', 'a', '=', '<init', 'b', '=', '$a32233bb', NL, 'rt_ci', '=', 1, NL, ENDIF, NL,
            IF, 'ChecksumEquals', 'a', '=', '<init', 'b', '=', '$13e9232a', NL, 'rt_ci', '=', 1, NL, ENDIF, NL,
        ENDIF, NL,
        IF, '(', '<rt_ci', '=', 1, ')', NL,
            IF, 'GotParam', '$5a76e075', NL, 'GoalManager_EditGoal', 'name', '=', '<goal', 'params', '=', '{', '$6f291b42', '=', 0, '}', NL, ENDIF, NL,
            IF, 'GotParam', 'goal_collect_objects', NL,
                'GetArraySize', '<goal_collect_objects', NL, 'rt_c', '=', 0, NL,
                IF, '(', '<array_size', '>', 0, ')', NL, BEGIN, NL,
                    IF, 'StructureContains', 'structure', '=', '(', '<goal_collect_objects', '[', '<rt_c', ']', ')', 'Flag', NL,
                        'rt_fl', '=', '(', '(', '<goal_collect_objects', '[', '<rt_c', ']', ')', '.', 'Flag', ')', NL,
                        'GoalManager_SetGoalFlag', 'name', '=', '<goal', '<rt_fl', 0, NL,
                    ENDIF, NL,
                    'rt_c', '=', '(', '<rt_c', '+', 1, ')', NL,
                REPEAT, '<array_size', NL, ENDIF, NL,
            ENDIF, NL,
            'GoalManager_UninitializeGoal', 'name', '=', '<goal', NL,
            'GoalManager_InitializeGoal', 'name', '=', '<goal', NL,
            'Printf', S_('RTSR collect reset %g'), 'g', '=', '<goal', NL,
        ENDIF, NL, ENDSCRIPT])

def P_(x, y):
    from qbasm import P
    return P(x, y)

def patch_pause_best(d):
    # gamemenu_pause $8b0789f0 draws the upper-left pause stats (Cash, Career Total; best combo is another script). In speedrun:
    # one "Best Time - M:SS" line in the cash line's place, then return (inserted before `IF GameModeEquals Classic / cash = 0`).
    from qbasm import asm, S, NL, IF, ELSE, ENDIF, RETURN
    from qbsplice import splice
    p = next(p for p, t, n in items(d) if t == 7 and n == 0x8b0789f0)
    s = script_at(d, p)
    at = b'\x16' + struct.pack('<I', 0x20985922) + b'\x16' + struct.pack('<I', 0x5a2c589a) + b'\x01'
    i = s.index(at) - 3; assert s.count(at) == 1 and s[i] == 0x47 and s[i-1] == 1
    add = asm(sr_wrap(['GetCurrentLevel', NL, 'rt_speedrun_best_text', 'level', '=', '<level', NL,
        'FormatText', 'TextName', '=', '$f9f29155', S('Best Time - %a'), 'a', '=', '<rt_bm', NL,
        'CreateScreenElement', '{', NL, 'TYPE', '=', '$5200dfb6', NL, 'parent', '=', '$4aa5f466', NL, 'id', '=', '$603179db', NL,
            'text', '=', '<$f9f29155', NL, 'Font', '=', 'Dialog', NL, 'Just', '=', '[', 'Right', ',', 'Top', ']', NL,
            'Scale', '=', P_(0.8, 0.95), NL, 'Pos', '=', P_(210.0, 26.0), NL, 'rgba', '=', '[', 128, 128, 128, 78, ']', NL,
            '$57710f31', '=', 20, NL, '$ed344e1b', '=', 4, NL, '}', NL,
        'CreateScreenElement', '{', NL, 'TYPE', '=', 'SpriteElement', NL, 'parent', '=', '$4aa5f466', NL, 'id', '=', '$d75cc930', NL,
            'TEXTURE', '=', '$0736e962', NL, 'rgba', '=', '[', 0, 0, 0, 100, ']', NL, 'Just', '=', '[', 'Right', ',', 'Top', ']', NL,
            'Pos', '=', P_(285.0, 35.0), NL, 'Scale', '=', P_(14.0, 4.0), NL, '$57710f31', '=', 19, NL, '$ed344e1b', '=', 4, NL, '}', NL] + ([
        # Evan: a BEST TIME message that stays in the middle of the screen (it belongs to the menu, so it goes away with it);
        # a new best from the run that just ended also gets the NEW BEST TIME!! popup + jingle just above it, once
        'FormatText', 'TextName', '=', 'rt_bc', S('BEST TIME: %a'), 'a', '=', '<rt_bm', NL,
        IF, 'ScreenElementExists', 'id', '=', 'rt_sr_best_center', NL, 'DestroyScreenElement', 'id', '=', 'rt_sr_best_center', NL, ENDIF, NL,
        'CreateScreenElement', '{', NL, 'TYPE', '=', '$5200dfb6', NL, 'parent', '=', '$4aa5f466', NL, 'id', '=', 'rt_sr_best_center', NL,
            'text', '=', '<rt_bc', NL, 'Font', '=', 'Dialog', NL, 'Just', '=', '[', 'center', ',', 'center', ']', NL,
            'Scale', '=', 1.4, NL, 'Pos', '=', P_(290.0, 230.0), NL, 'rgba', '=', '[', 128, 128, 128, 110, ']', NL,
            '$57710f31', '=', 20, NL, '}', NL,
        # (the themed panel message never showed while paused, so the text is a plain element of the menu, like the line above)
        IF, '(', 'rt_sr_has', '=', 1, ')', NL, 'change', 'rt_sr_has', '=', 0, NL,
        IF, '(', 'rt_sr_nb', '=', 1, ')', NL, 'change', 'rt_sr_nb', '=', 0, NL,
            'CreateScreenElement', '{', NL, 'TYPE', '=', '$5200dfb6', NL, 'parent', '=', '$4aa5f466', NL, 'id', '=', 'rt_sr_newbest_text', NL,
                'text', '=', S('NEW BEST TIME!!'), NL, 'Font', '=', 'Dialog', NL, 'Just', '=', '[', 'center', ',', 'center', ']', NL,
                'Scale', '=', 1.6, NL, 'Pos', '=', P_(290.0, 190.0), NL, 'rgba', '=', '[', 128, 104, 16, 128, ']', NL,
                '$57710f31', '=', 21, NL, '}', NL,
            'SpawnScriptNow', 'rt_speedrun_newbest', NL, ELSE, NL,
            'rt_speedrun_fmt_ms', 'ms', '=', '(', 'rt_sr_run', ')', NL, 'FormatText', 'TextName', '=', 'rt_tr', S('This run: %a'), 'a', '=', '<rt_fm', NL,
            'CreateScreenElement', '{', NL, 'TYPE', '=', '$5200dfb6', NL, 'parent', '=', '$4aa5f466', NL, 'id', '=', 'rt_sr_thisrun', NL,
                'text', '=', '<rt_tr', NL, 'Font', '=', 'Dialog', NL, 'Just', '=', '[', 'center', ',', 'center', ']', NL,
                'Scale', '=', 1.4, NL, 'Pos', '=', P_(290.0, 190.0), NL, 'rgba', '=', '[', 128, 128, 128, 120, ']', NL, '$57710f31', '=', 21, NL, '}', NL,
            ENDIF, NL, ENDIF, NL]
        if SAVE_BEST else []) + [
        RETURN, NL]))
    return relocate_script(d, 0x8b0789f0, splice(s, i, i, a4(add)), h('rt_old_pause_stats'))

def patch_levelselect_helpers(d):
    # levelselectmenu $63fe6729 builds the button hints and ends with `$c73a91e0 $e954a378 = <helpers`. In speedrun, use our own list
    # with left/right = Tour (inserted at top level just before that call).
    from qbasm import asm, S, NL
    from qbsplice import splice, find_stmt
    p = next(p for p, t, n in items(d) if t == 7 and n == 0x63fe6729)
    s = script_at(d, p)
    i = find_stmt(s, b'\x16' + struct.pack('<I', 0xc73a91e0) + b'\x16' + struct.pack('<I', 0xe954a378))
    hint = lambda b, t: ['{', 'buttons', '=', S(b), 'text', '=', S(t), '}', NL]
    add = asm(sr_wrap(['helpers', '=', '[', NL] + hint('\\b7/\\b4', 'Select') + hint('\\b6/\\b5', 'Tour') +
                      hint('\\m1', 'Back') + hint('\\m0', 'Accept') + [']', NL]))
    return relocate_script(d, 0x63fe6729, splice(s, i, i, a4(add)), h('rt_old_levelselect_helpers'))

def patch_runmenu(d):
    # skateshop.qb $f69fbfca builds the menu after skater select (title by game mode, PLAY LEVEL / EDIT TRICKS / BUY STATS / SKATE SHOP /
    # UPDATE INITIALS). In speedrun: title SPEEDRUN MODE, no stats item (stats are fixed at 10), no skate shop.
    from qbasm import asm, S, NL, IF, ENDIF
    from qbsplice import splice, find_stmt
    N = lambda x: b'\x16' + struct.pack('<I', x)
    GME = N(0x20985922)
    p = next(p for p, t, n in items(d) if t == 7 and n == 0xf69fbfca)
    s = script_at(d, p)
    # 1. title: before the first `IF GameModeEquals $417149c8` (right after the title SWITCH)
    i = s.index(GME + N(0x417149c8)) - 3; assert s[i] == 0x47 and s[i-1] == 1
    s = splice(s, i, i, a4(asm(sr_wrap(['<$4565a4f3', '=', S('SPEEDRUN MODE'), NL]))))
    # 2. stats item: shown when $8c330312 = 1 (set for Classic just before). Clear it in speedrun.
    cond = b'\x0e\x2d' + N(0x8c330312) + b'\x07\x17' + struct.pack('<i', 1) + b'\x0f'
    i = s.index(cond) - 3; assert s.count(cond) == 1 and s[i] == 0x47 and s[i-1] == 1
    s = splice(s, i, i, a4(asm(sr_wrap(['$8c330312', '=', 0, NL]))))
    # 3. skate shop: `IF GameModeEquals Classic / IF $d88fa42b (THPS2 tour)`. The THPS2 check is used all over, so instead the outer
    #    condition becomes ( <rt_shop = 1 ), with rt_shop = Classic and not speedrun, computed just before it.
    j = s.index(N(0xd88fa42b) + b'\x01'); assert s.count(N(0xd88fa42b)) == 1
    i = s.rindex(b'\x47', 0, j)
    while s[i+3:i+13] != GME + N(0x5a2c589a): i = s.rindex(b'\x47', 0, i)
    assert s[i-1] == 1
    s = splice(s, i + 3, i + 13, b'\x0e\x2d' + N(h('rt_shop')) + b'\x07\x17' + struct.pack('<i', 1) + b'\x0f')
    pre = asm(['rt_shop', '=', 0, NL, IF, 'GameModeEquals', '$5a2c589a', NL, 'rt_shop', '=', 1, NL, ENDIF, NL] + sr_wrap(['rt_shop', '=', 0, NL]))
    s = splice(s, i, i, a4(pre))
    return relocate_script(d, 0xf69fbfca, s, h('rt_old_run_menu'))

def patch_levelselect_tours(d):
    # in speedrun, after $94559c4d sets its pad handlers, replace them with the free-skate set whose left/right cycle tours
    from qbasm import asm, NL, S
    p = next(p for p, t, n in items(d) if t == 7 and n == LS_BUILD)
    s = script_at(d, p)
    at = (b'\x16' + struct.pack('<I', h('DoScreenElementMorph')) + b'\x16' + struct.pack('<I', h('id')) + b'\x07\x16' +
          struct.pack('<I', 0xebdf3e7d) + b'\x16' + struct.pack('<I', h('alpha')) + b'\x07\x17' + struct.pack('<i', 1))
    assert s.count(at) == 1, s.count(at)
    i = s.index(at); assert s[i-1] == 1
    add = asm(sr_wrap(['SetScreenElementProps', '{', NL, 'id', '=', '$ebdf3e7d', NL, '$475bf03c', '=', '[', NL,
        '{', '$30a4f836', '$004ff0d1', '}', NL, '{', '$0fd1ac26', '$fb3665b2', '}', NL,
        '{', '$6949db75', 'rt_speedrun_tour_cycle', 'params', '=', '{', 'left', '}', '}', NL,
        '{', '$05ddd87c', 'rt_speedrun_tour_cycle', 'params', '=', '{', 'Right', '}', '}', NL,
        '{', '$7ee0fd2a', '$81abda01', '}', NL, ']', NL, 'replace_handlers', NL, '}', NL] + (
        # Free Skate's category bar ($e66a9d02, created hidden in Classic by $87fd4d45): "< TOUR 2/5 >" plus the tour name
        ['DoScreenElementMorph', 'id', '=', '$e66a9d02', 'alpha', '=', 1, NL,
         'DoScreenElementMorph', 'id', '=', '$799cd7cb', 'alpha', '=', 1, NL,
         'DoScreenElementMorph', 'id', '=', '$291493fb', 'alpha', '=', 1, NL,
         'rt_speedrun_tour_label', NL] if MENU_POLISH else [])))
    if MENU_POLISH:
        d = add_script(d, h('rt_speedrun_tour_label'), tour_label_script())
    dbg = os.environ.get('TOUR_DEBUG', '')
    if dbg == 'reloc_only':          # bisect: relocate unchanged
        return relocate_script(d, LS_BUILD, s, h('rt_old_levelselect_build'))
    d = add_script(d, h('rt_speedrun_tour_cycle'), tour_cycle_script())
    if dbg == 'add_only':
        return d
    return relocate_script(d, LS_BUILD, s[:i] + a4(add) + s[i:], h('rt_old_levelselect_build'))

def patch_menu(d):
    if MENU_ENTRY:
        return patch_menu_entry_helpers(d)
    # HIJACK='0546ca93:665d8f5c,6d344afd:52de104c' -> in those scripts, the SetGameType id (little-endian hex) becomes MODE_ID
    spec = [x.split(':') for x in os.environ.get('HIJACK', '').split(',') if x]
    if SR_GLOBAL:
        # keep SetGameType Classic; the Classic menu script just also turns speedrun on (an inert new global, not $e9c2cd10)
        from qbasm import asm, NL
        d = add_global(d, h('rt_speedrun_on'), 'int', 1 if SR_DEFAULT_ON else 0)
        for sh, _ in spec:
            sh = int(sh, 16)
            p = next(p for p, t, n in items(d) if t == 7 and n == sh)
            s = script_at(d, p); assert s[0] == 1
            d = relocate_script(d, sh, s[:1] + a4(asm(['change', 'rt_speedrun_on', '=', 1, NL])) + s[1:], h('rt_old_%08x' % sh))
        return d
    for sh, old in spec:
        sh = int(sh, 16); old = bytes.fromhex(old); new = struct.pack('<I', MODE_ID)
        for p, t, name in items(d):
            if t == 7 and name == sh:
                sc = script_at(d, p); assert sc.count(old) == 1, (hex(sh), sc.count(old))
                sc = sc.replace(old, new)
                if os.environ.get('UNLOCK_LEVELS') and sh == 0x6d344afd:
                    # drop the debug Printf (to make room in the fixed-size slot) and add: change $e9c2cd10 = 1  (all levels unlocked in level select)
                    call = b'\x01\x16\xab\xfe\x51\x5c'          # newline + call of script $5c51feab
                    i = sc.index(call)
                    chg = b'\x16\xdf\x01\xa8\xbf\x16\x10\xcd\xc2\xe9\x07\x17\x01\x00\x00\x00'
                    # the slot needs an exact stream size, so repeat the (idempotent) statement until it fits
                    for rep in range(1, 20):
                        try:
                            d = replace_script(d, sh, b'\x01' + (b'\x01' + chg) * rep + sc[i:]); break
                        except ValueError:
                            continue
                    else:
                        raise SystemExit('could not fit unlock statement')
                    break
                d = replace_script(d, sh, sc); break
        else:
            raise SystemExit('script %x not found' % sh)
    return d

if __name__ == '__main__':
    pak, pab, work, outdir = sys.argv[1:5]
    files = os.path.join(work, 'files')
    shutil.rmtree(work, ignore_errors=True)
    subprocess.check_call([sys.executable, '-I', os.path.join(here, 'qbpack.py'), 'unpack', pak, pab, files])
    gm = os.path.join(files, 'scripts', 'gamemodes', 'gamemode.qb.wpc')
    st = os.path.join(files, 'scripts', 'game', 'startup.qb.wpc')
    mn = os.path.join(files, 'scripts', 'game', 'menu', 'gamemenu_helpers.qb.wpc')
    ls = os.path.join(files, 'scripts', 'game', 'menu', 'levelselectmenu.qb.wpc')
    tsr = os.path.join(files, 'scripts', 'game', 'goals', 'goal_timed_score_run.qb.wpc')
    jobs = ([] if SR_GLOBAL else [(gm, patch_gamemode), (st, patch_startup)]) + ([(mn, patch_menu)] if os.environ.get('HIJACK') or MENU_ENTRY else []) + ([(ls, patch_levelselect)] if os.environ.get('UNLOCK_FLAG') else [])
    jobs += [(tsr, patch_goaltime)] if os.environ.get('GOAL_TIME') else []
    jobs += [(tsr, patch_clock)] if os.environ.get('COUNTUP_CLOCK') else []
    jobs += [(os.path.join(files, 'scripts', 'game', 'menu', 'gameplayoptions.qb.wpc'), patch_options_row)] if CLASSIC_TIMER else []
    jobs += [(tsr, patch_classic_hide)] if CLASSIC_TIMER and CLASSIC_HIDE else []
    gu = os.path.join(files, 'scripts', 'game', 'goal_utilities.qb.wpc')
    cm = os.path.join(files, 'scripts', 'game', 'menu', 'classic_mode_menu.qb.wpc')
    jobs += [(cm, patch_levelcomplete)] if FINISH and SR_GLOBAL else [(gu, patch_success_hook)] if FINISH else []
    jobs += [(cm, patch_runstart)] if RESET_GOALS or STATS_MAX else []
    jobs += [(gu, patch_tour_diff)] if PARK_TOUR and TOUR_DIFF else []
    jobs += [(ls, patch_levelselect_tours)] if TOUR_SWITCH else []
    jobs += [(ls, patch_levelinfo_best)] if SHOW_BEST else []
    FILES = files
    jobs += [(ls, patch_level_items_comp)] if COMP_GREY else []
    mdir = os.path.join(files, 'scripts', 'game', 'menu')
    jobs += [(os.path.join(mdir, 'rt_tour_select.qb.wpc'), patch_tour_options), (cm, patch_record_screen), (cm, patch_skip_goalspage),(os.path.join(mdir, 'gamemenu_pause.qb.wpc'), patch_pause_best), (os.path.join(mdir, 'gamemenu_pause.qb.wpc'), patch_pause_cash), (os.path.join(mdir, 'gamemenu_pause.qb.wpc'), patch_pause_combo)] if RUN_END else []
    jobs += [(os.path.join(files, 'scripts', 'game', 'menu', 'rt_tour_select.qb.wpc'), patch_tour_card)] if TOUR_TOTAL else []
    jobs += [(ls, patch_levelselect_helpers), (os.path.join(files, 'scripts', 'game', 'menu', 'skateshop.qb.wpc'), patch_runmenu)] if MENU_POLISH else []
    jobs += [(os.path.join(files, 'scripts', 'game', 'menu', 'stats.qb.wpc'), patch_statnodes)] if STATS_OFF else []
    jobs += [(os.path.join(files, 'scripts', 'game', 'goals', 'goal_all_goals.qb.wpc'), patch_cash)] if CASH_OFF else []
    jobs += [(os.path.join(files, 'scripts', 'game', 'goals', 'goal_all_goals.qb.wpc'), patch_allgoals)] if HIDE_ALLGOALS else []
    if os.environ.get('PROBE_TOUR'):     # TEST ONLY: dump tour/level structs to debug.txt from the tour card builder and the level info panel
        def probe_tour(d):
            from qbasm import asm, S, NL
            p = next(p for p, t, n in items(d) if t == 7 and n == 0xd0d8ce3d); s = script_at(d, p)
            add = asm(['Printf', S('RTSR probe tour card'), NL, '$cb85030f', NL, 'PrintStruct', '<$70e3cf3b', NL,
                       'RTCM_GetTourInfo', NL, 'Printf', S('RTSR probe current tour info'), NL, 'PrintStruct', '<$15462e88', NL,
                       'RTCM_GetTourInfo', 'id', '=', 'thps3', NL, 'Printf', S('RTSR probe tour info id=thps3'), NL, 'PrintStruct', '<$15462e88', NL])
            assert s[0] == 3; i = s.index(b'\x04\x01') + 2
            return relocate_script(d, 0xd0d8ce3d, s[:i] + a4(add) + s[i:], h('rt_old_tour_card'))
        def probe_level(d):
            from qbasm import asm, S, NL, IF, ENDIF, ENDSCRIPT
            d = add_script(d, h('rt_probe_level'), asm([NL, 'Printf', S('RTSR probe level %g'), 'g', '=', '<level', NL,
                'RTCM_LevelGetInfo', 'level', '=', '<level', NL, 'PrintStruct', '<$5d5335b9', NL, ENDSCRIPT]))
            p = next(p for p, t, n in items(d) if t == 7 and n == 0xd8ac364b); s = script_at(d, p)
            add = asm([IF, 'GotParam', 'level', NL, 'rt_probe_level', 'level', '=', '<level', NL, ENDIF, NL])
            return relocate_script(d, 0xd8ac364b, s[:-1] + a4(add) + b'\x24', h('rt_old_level_info_probe'))
        jobs += [(os.path.join(files, 'scripts', 'game', 'menu', 'rt_tour_select.qb.wpc'), probe_tour), (ls, probe_level)]
    for path, fn in jobs:
        data = open(path, 'rb').read()
        new = fn(data)
        open(path, 'wb').write(new)
        print(os.path.basename(path), len(data), '->', len(new))
    os.makedirs(outdir, exist_ok=True)
    out = [os.path.join(outdir, 'qb.pak.wpc'), os.path.join(outdir, 'qb.pab.wpc')]
    if os.environ.get('PACK') == 'stable':     # keep files at their original offsets, grown files go to the end
        subprocess.check_call([sys.executable, '-I', os.path.join(here, 'qbpack.py'), 'pack_stable', pak, pab, files] + out)
    else:
        subprocess.check_call([sys.executable, '-I', os.path.join(here, 'qbpack.py'), 'pack', pak, files] + out)
    if NORM_NL: script_at = _raw_script_at   # z_mainmenu scripts are still old-compiler output; read them as stored
    if MENU_ENTRY:   # the original zone pak is expected next to the original qb.pak.wpc (ORIGINALMODBACKUPS); install to data/mod/Zones/z_mainmenu/
        build_mainmenu_pak(os.path.join(os.path.dirname(os.path.abspath(pak)), 'z_mainmenu.pak.wpc'), os.path.join(outdir, 'z_mainmenu.pak.wpc'))
