# Register daily Quant_Agent monitor at 15:40 local time.
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

$taskName = "QuantAgentDailyNotify"
$arg = "`"$root\scripts\daily_notify.py`""
$action = New-ScheduledTaskAction -Execute $python -Argument $arg -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At 15:40
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "OK: 已註冊工作排程 '$taskName'，每日 15:40 執行"
Write-Host "測試: python `"$root\scripts\daily_notify.py`" --force"
Write-Host "刪除: Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
