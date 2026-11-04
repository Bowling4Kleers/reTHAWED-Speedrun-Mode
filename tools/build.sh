#!/bin/bash
# Linux/macOS equivalent of build.ps1.  Usage: tools/build.sh <base dir with stock qb.pak.wpc, qb.pab.wpc, z_mainmenu.pak.wpc> <out dir> [NORM_NL=1 ...]
set -e
here="$(cd "$(dirname "$0")" && pwd)"; base=$1; out=$2; shift 2
export SR_GLOBAL=1 UNLOCK_FLAG=rt_speedrun GOAL_TIME=3599 GOAL_TIME_MODE=relocate HIDE_TIMER=1 COUNTUP_CLOCK=1 PACK=stable CLOCK_STYLE=theme \
  FINISH_ON_ALL=1 RESET_GOALS=1 CASH_OFF=1 STATS_MAX=10 STATS_OFF=1 HIDE_ALLGOALS=1 MENU_ENTRY=1 TOUR_SWITCH=1 SAVE_BEST=1 PARK_TOUR=1 \
  SHOW_BEST=1 MENU_POLISH=1 COMP_GREY=1 TOUR_TOTAL=1 RUN_END=1 COLLECT_RESET=1 CLASSIC_TIMER=1 CLASSIC_HIDE=1 CLASSIC_HC_LOCK=1 FLAG_RESET=1 TOUR_DIFF=1
for kv in "$@"; do export "$kv"; done
work="${TMPDIR:-/tmp}/speedrun_work_$(basename "$out")"
python3 -I "$here/build_speedrun_v1.py" "$base/qb.pak.wpc" "$base/qb.pab.wpc" "$work" "$out"
