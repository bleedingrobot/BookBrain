<#
.SYNOPSIS
  Registers (or updates) the "BookBrain Nightly" Windows Scheduled Task.

.DESCRIPTION
  The task runs `python -m app.jobs.nightly` from the backend's virtualenv
  once a day. That entrypoint does the whole unattended pipeline — pull the
  Torrents folder, scan the Book Dump, auto-organize what clears the
  confidence bar, refresh covers + the library index — and never touches the
  review queue or duplicates. Output is appended to backend\nightly-runs.log.

  This is the "server is usually off overnight" path. If the BookBrain
  backend happens to be running at the scheduled hour, its own in-process
  scheduler covers it instead; the two guard against running at the same
  time, so having both on is fine.

.PARAMETER Hour
  Hour of day (0-23, local time) to run. Default 2 (2am). Set this to the
  same hour you picked in the app's Settings > Nightly run, so both paths
  agree.

.EXAMPLE
  Right-click register-nightly-task.bat > Run as administrator
#>
param(
    [ValidateRange(0, 23)]
    [int]$Hour = 2
)

$ErrorActionPreference = "Stop"

$BackendDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
$TaskName = "BookBrain Nightly"

if (-not (Test-Path $Python)) {
    Write-Error "Can't find the backend virtualenv Python at:`n  $Python`n`nCreate it first:  cd `"$BackendDir`"  then  python -m venv .venv  and  .venv\Scripts\pip install -e `".[dev]`""
    exit 1
}

$At = (Get-Date -Hour $Hour -Minute 0 -Second 0)

$action = New-ScheduledTaskAction -Execute $Python -Argument "-m app.jobs.nightly" -WorkingDirectory $BackendDir
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "BookBrain unattended nightly run: scan, auto-organize, covers, index." -Force | Out-Null

Write-Host ""
Write-Host "Registered '$TaskName' — runs daily at $($At.ToString('HH:mm')) local time." -ForegroundColor Green
Write-Host "  Python:      $Python"
Write-Host "  Working dir: $BackendDir"
Write-Host "  Log file:    $(Join-Path $BackendDir 'nightly-runs.log')"
Write-Host ""
Write-Host "Run it now to test:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Remove it:           .\unregister-nightly-task.ps1"
