# reTHAWed Speedrun Mode

A Speedrun mode for [reTHAWed](https://rethawed.com) (Tony Hawk's American Wasteland), in the style of the THPS 1+2 / 3+4 remakes:

- A **Speedrun mode** icon on the main menu, then character select, then a Free Skate-style level select with every level open.
- Classic goals with per-run progress, so your Classic save data is never overwritten.
- The clock counts **up** and the run ends when all 10 goals are done.
- Level select shows **Best time** for each level (a hyphen if you haven't finished it yet), and a tour total.

## Install

Pick the folder under `releases/` that matches your reTHAWed version (shown in `<game>\version.txt`):

| reTHAWed | Folder |
|---|---|
| 5.0.1.0 | [`releases/5.0.1.0`](releases/5.0.1.0) |

1. Back up your `save` folder and these three game files.
2. Copy `qb.pak.wpc` and `qb.pab.wpc` to `<game>\data\mod\`.
3. Copy `z_mainmenu.pak.wpc` to `<game>\data\mod\Zones\z_mainmenu\`.

A reTHAWed update overwrites these files, so after an update use the release that matches the new version. Don't mix files from different versions.

## Build from source

The builder patches the stock reTHAWed `qb.pak.wpc` / `qb.pab.wpc` / `z_mainmenu.pak.wpc` from your own install. Each patch is found by script name and token pattern, not by offset, so it usually carries over to new reTHAWed versions.

```
tools/build.sh <folder with the 3 stock files> <out folder> NORM_NL=1
```

`NORM_NL=1` is needed for reTHAWed 5.0.1.0 and later, because their script compiler writes every newline token twice. On Windows, `tools\build.ps1` does the same thing from the original work folder layout. Python 3.8+ with the standard library is enough.

`tools/qbrt.py` is a lossless QB decompiler/recompiler for reTHAWed script containers, used to check each build (`qbrt.py verify`).
