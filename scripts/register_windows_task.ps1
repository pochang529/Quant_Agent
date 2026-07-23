# Register Quant_Agent intraday monitor: Mon–Fri every 15 min during 09:00–13:30.
# Script itself also skips outside market hours (no API).
# Run once (Admin PowerShell recommended):
#   powershell -ExecutionPolicy Bypass -File scripts/register_windows_task.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    $python = (Get-Command py -ErrorAction SilentlyContinue).Source
}
if (-not $python) {
    Write-Error "找不到 python，請先安裝並加入 PATH"
}

$taskName = "QuantAgentIntradayNotify"
# Remove old 15:40 task name if present
Unregister-ScheduledTask -TaskName "QuantAgentDailyNotify" -Confirm:$false -ErrorAction SilentlyContinue

$arg = "`"$root\scripts\daily_notify.py`""
$action = New-ScheduledTaskAction -Execute $python -Argument $arg -WorkingDirectory $root

# Start Mon 09:00, repeat every 15 min for 4h30m (= until ~13:30)
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 09:00
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 09:00 -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Hours 4 -Minutes 35)).Repetition

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "OK: 已註冊 '$taskName'＝週一至五 09:00 起每 15 分（至約 13:30）"
Write-Host "腳本會再擋非盤中時段，不打 API。"
Write-Host "測試: python `"$root\scripts\daily_notify.py`" --force"
Write-Host "刪除: Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
