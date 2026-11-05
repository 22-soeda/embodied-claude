<#
.SYNOPSIS
    Register (or remove) the autonomous heartbeat in Windows Task Scheduler.

.DESCRIPTION
    The Windows stand-in for the crontab line in autonomous-action.sample.sh.
    Runs autonomous-action.sh through Git Bash every -IntervalMinutes, aligned
    to the hour (:00, :20, :40 for 20), because the script's own schedule
    control only lets off-peak runs through at minute 00.

    The task runs only while you are logged on, so the toast from the notify
    server reaches your desktop. A sleeping PC is not woken; missed beats are
    simply skipped. Git Bash is started through `conhost --headless` so no
    console window flashes up.

.EXAMPLE
    powershell -NoProfile -File scripts\windows\register-heartbeat.ps1
    powershell -NoProfile -File scripts\windows\register-heartbeat.ps1 -IntervalMinutes 60
    powershell -NoProfile -File scripts\windows\register-heartbeat.ps1 -Unregister
#>
param(
    [ValidateSet(10, 15, 20, 30, 60)]
    [int] $IntervalMinutes = 20,
    [switch] $Unregister
)

$ErrorActionPreference = "Stop"

$taskPath = "\EmbodiedClaude\"
$taskName = "heartbeat"

if ($Unregister) {
    Unregister-ScheduledTask -TaskPath $taskPath -TaskName $taskName -Confirm:$false
    Write-Output "removed $taskPath$taskName"
    return
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$script = Join-Path $repo "autonomous-action.sh"
if (-not (Test-Path $script)) {
    throw "autonomous-action.sh not found: run scripts/windows/configure_v0.py first"
}

$bash = Join-Path $env:ProgramFiles "Git\bin\bash.exe"
if (-not (Test-Path $bash)) {
    throw "Git Bash not found at $bash"
}

# C:\Users\me\repo\autonomous-action.sh -> /c/Users/me/repo/autonomous-action.sh
$posixScript = "/" + $script.Substring(0, 1).ToLower() + ($script.Substring(2) -replace "\\", "/")

# -l gives a login shell, so /usr/bin comes first on PATH (timeout, od, find).
$action = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless `"$bash`" -l -c `"$posixScript`"" `
    -WorkingDirectory $repo

$now = Get-Date
$nextSlot = [math]::Floor($now.Minute / $IntervalMinutes) * $IntervalMinutes + $IntervalMinutes
$start = $now.Date.AddHours($now.Hour).AddMinutes($nextSlot)
$trigger = New-ScheduledTaskTrigger -Once -At $start `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)

# The script itself gives claude -p 20 minutes; leave room for setup and logging.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskPath $taskPath -TaskName $taskName `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "embodied-claude heartbeat: $posixScript every $IntervalMinutes min" `
    -Force | Out-Null

$info = Get-ScheduledTaskInfo -TaskPath $taskPath -TaskName $taskName
Write-Output "registered $taskPath$taskName every $IntervalMinutes min, next run $($info.NextRunTime)"
