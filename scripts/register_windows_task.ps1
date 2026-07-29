# Register two parallel Quant_Agent push paths:
# 1) Trigger monitor every 15 minutes during market hours.
# 2) Fixed reports at 09:00, 11:30, and 15:40.
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

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
$days = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
$scriptPath = "`"$root\scripts\daily_notify.py`""

$taskNames = @(
    "QuantAgentIntradayNotify",
    "QuantAgentTriggerMorning",
    "QuantAgentTriggerAfternoon",
    "QuantAgentFixedOpen",
    "QuantAgentFixedMidday",
    "QuantAgentFixedClose"
)
foreach ($name in $taskNames + @("QuantAgentDailyNotify")) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
}

function New-RepeatingTrigger($at, $duration) {
    $weekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $at
    $weekly.Repetition = (
        New-ScheduledTaskTrigger -Once -At $at `
            -RepetitionInterval (New-TimeSpan -Minutes 15) `
            -RepetitionDuration $duration
    ).Repetition
    return $weekly
}

function Register-QuantTask($name, $arguments, $trigger) {
    $action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $root
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
}

# 09:00 and 11:30 are handled by fixed reports, so trigger checks start after them.
$morningTrigger = New-RepeatingTrigger "09:15" (New-TimeSpan -Hours 2)
$afternoonTrigger = New-RepeatingTrigger "11:45" (New-TimeSpan -Hours 1 -Minutes 50)
Register-QuantTask "QuantAgentTriggerMorning" $scriptPath $morningTrigger
Register-QuantTask "QuantAgentTriggerAfternoon" $scriptPath $afternoonTrigger

Register-QuantTask "QuantAgentFixedOpen" "$scriptPath --fixed open" (
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 09:00
)
Register-QuantTask "QuantAgentFixedMidday" "$scriptPath --fixed midday" (
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 11:30
)
Register-QuantTask "QuantAgentFixedClose" "$scriptPath --fixed close" (
    New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At 15:40
)

Write-Host "OK: 已註冊達標監測＝盤中每 15 分鐘（固定推播時點除外）"
Write-Host "OK: 已註冊固定推播＝09:00 開盤／11:30 盤中／15:40 收盤"
Write-Host "測試: python `"$root\scripts\daily_notify.py`" --force"
Write-Host "刪除: 請移除 QuantAgentTrigger* 與 QuantAgentFixed* 排程"
