# Build a speedrun version with the full current env.  Usage: tools\build.ps1 <outdir name> [extra env assignments like AUTOWIN=1 SR_DEFAULT_ON=1 MENU_POLISH=] [-Base <folder of stock qb.pak/qb.pab/z_mainmenu.pak>]
# -Base defaults to ORIGINALMODBACKUPS (reTHAWed 5.0.0.2 release). For the 2026-11-01 source build use -Base ORIGINALMODBACKUPS_5002, for release 5.0.1.0 use -Base ORIGINALMODBACKUPS_5010 (sets NORM_NL=1 itself).
param([string]$Out, [string[]]$Extra = @(), [string]$Base = "ORIGINALMODBACKUPS")
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot
$py = "C:\Users\Evan\AppData\Local\Programs\Python\Python38\python.exe"
$envs = [ordered]@{ SR_GLOBAL='1'; UNLOCK_FLAG='rt_speedrun'; GOAL_TIME='3599'; GOAL_TIME_MODE='relocate'; HIDE_TIMER='1'; COUNTUP_CLOCK='1'
  PACK='stable'; CLOCK_STYLE='theme'; FINISH_ON_ALL='1'; RESET_GOALS='1'; CASH_OFF='1'; STATS_MAX='10'; STATS_OFF='1'; HIDE_ALLGOALS='1'
  MENU_ENTRY='1'; TOUR_SWITCH='1'; SAVE_BEST='1'; PARK_TOUR='1'; SHOW_BEST='1'; MENU_POLISH='1'; COMP_GREY='1'; TOUR_TOTAL='1'; RUN_END='1'; COLLECT_RESET='1'; CLASSIC_TIMER='1'; CLASSIC_HIDE='1'; CLASSIC_HC_LOCK='1'; FLAG_RESET='1'; TOUR_DIFF='1' }
if ($Base -like '*5010*') { $envs['NORM_NL'] = '1' }   # 5.0.1.0's compiler doubles every newline token
foreach ($e in $Extra) { $k, $v = $e.Split('=', 2); $envs[$k] = $v }
foreach ($k in @($envs.Keys)) { if ($envs[$k]) { Set-Item "env:$k" $envs[$k] } else { Remove-Item "env:$k" -ErrorAction SilentlyContinue } }
$work = Join-Path $env:TEMP "speedrun_work_$Out"
& $py -I "$PSScriptRoot\build_speedrun_v1.py" "$root\$Base\qb.pak.wpc" "$root\$Base\qb.pab.wpc" $work "$root\$Out"
if ($LASTEXITCODE) { throw "build failed" }
foreach ($k in @($envs.Keys)) { Remove-Item "env:$k" -ErrorAction SilentlyContinue }




