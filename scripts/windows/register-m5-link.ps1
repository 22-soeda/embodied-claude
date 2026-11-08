<#
.SYNOPSIS
    Start m5-link at logon (and now), or remove that registration.

.DESCRIPTION
    m5-link is the desktop side of the M5 body: it holds the body's WebSocket,
    the conversation session and the hourly heartbeat. It must keep running,
    so it is registered in Task Scheduler to start when you log on, restart
    if it stops, and never time out. It runs only while you are logged on, so
    Windows notifications from the head still reach your screen.

    When m5-link runs, do not also register scripts\windows\register-heartbeat.ps1:
    the heartbeat would then run twice.

    Running the script again restarts m5-link, for example after `git pull`.
    It first ends any m5-link already running, together with everything below
    it, because Stop-ScheduledTask ends only conhost: uv, Python, the head
    (claude.exe), the MCP servers and the speech engine would keep running and
    hold the port.

.EXAMPLE
    powershell -NoProfile -File scripts\windows\register-m5-link.ps1
    powershell -NoProfile -File scripts\windows\register-m5-link.ps1 -Unregister
#>
param(
    [switch] $Unregister
)

$ErrorActionPreference = "Stop"

$taskPath = "\EmbodiedClaude\"
$taskName = "m5-link"

function Stop-RunningM5Link {
    # Match the uv launcher and its Python by their command line, then end each
    # whole process tree. `-m m5_link.body_mcp` (the head's MCP server) does not
    # match; it dies with the tree it belongs to.
    $roots = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -in "uv.exe", "python.exe" -and $_.CommandLine -match '-m m5_link"?\s*$'
    }
    foreach ($root in $roots) {
        taskkill /PID $root.ProcessId /T /F 2>&1 | Out-Null
    }
    if ($roots) { Write-Output "stopped the m5-link that was running" }
}

if (Get-ScheduledTask -TaskPath $taskPath -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskPath $taskPath -TaskName $taskName
}
Stop-RunningM5Link

if ($Unregister) {
    Unregister-ScheduledTask -TaskPath $taskPath -TaskName $taskName -Confirm:$false
    Write-Output "removed $taskPath$taskName"
    return
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$uv = (Get-Command uv -ErrorAction Stop).Source

# conhost --headless: no console window, and the process still gets a console
# for uv and Python to write to.
$action = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless `"$uv`" run --project m5-link --no-sync python -m m5_link" `
    -WorkingDirectory $repo

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskPath $taskPath -TaskName $taskName `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "embodied-claude m5-link: body WebSocket, conversation, heartbeat" `
    -Force | Out-Null

Start-ScheduledTask -TaskPath $taskPath -TaskName $taskName
Write-Output "registered and started $taskPath$taskName (log: m5-link\state\m5-link.log)"
