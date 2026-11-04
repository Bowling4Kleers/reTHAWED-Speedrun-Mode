Speedrun mode v49c for speedrun v49c rebuilt on the reTHAWed 5.0.1.0 release (game folder version.txt = 5.0.1.0, installed 2026-11-03).

Base files: <game>\data\mod\qb.pak.wpc, qb.pab.wpc and data\mod\Zones\z_mainmenu\z_mainmenu.pak.wpc as installed by the
5.0.1.0 update, copied to ORIGINALMODBACKUPS_5010. Same env as build.ps1 (all v49c switches incl. FLAG_RESET=1 TOUR_DIFF=1) plus NORM_NL=1.

Install: copy qb.pak.wpc + qb.pab.wpc to <game>\data\mod\ and z_mainmenu.pak.wpc to <game>\data\mod\Zones\z_mainmenu\.
Only for reTHAWed 5.0.1.0. Uninstall: put back the stock files (re-run the reTHAWed launcher's repair/update, or restore your own backup).

What changed vs the 5.0.0.2 port
- 5.0.1.0's qb paks are back to the release layout (500 named entries, no unnamed ones), but every script was compiled with the new
  compiler, which writes each newline token twice (01 01). That broke the builder's token patterns (first failure: patch_goaltime's
  end-of-script check on $d0b3442c).
- New builder switch NORM_NL=1: scripts are read with each run of 2n newline tokens collapsed to n (qbrt disassemble -> assemble),
  so the existing patterns match. Newline tokens are no-ops to the engine. Unpatched scripts are left exactly as stored.
  z_mainmenu scripts are still old-compiler output, so they are read as stored.
- build.ps1 sets NORM_NL=1 by itself when -Base contains 5010:  tools\build.ps1 build_v49c_rethawed5010 -Base ORIGINALMODBACKUPS_5010

Checks done
- Updated tools still rebuild the release v49c byte for byte (5add78fc / 59f67527 / b94d54c4).
- New build changes the same 11 qb files as v49c; every other file decompiles identically to stock 5.0.1.0.
- Patched scripts, decompiled with blank lines ignored, are identical to release v49c's apart from 5.0.1.0's own stock change
  (goal_utilities $d04a1451 AutoKick check, same as the 2026-11-01 source).
- Stock 5.0.1.0 vs 5.0.0.2: of the 11 patched files only goal_utilities changed in substance; the rest differ only in newline tokens.
- qbrt verify passes on the output (9,394 scripts round-trip).
- z_mainmenu: only z_mainmenu_scripts.qb.wpc differs from stock 5.0.1.0, and it is byte-identical to v49c's.
Not playtested.
