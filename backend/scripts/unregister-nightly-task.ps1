<#
.SYNOPSIS
  Removes the "BookBrain Nightly" Windows Scheduled Task.
#>
$ErrorActionPreference = "Stop"
$TaskName = "BookBrain Nightly"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed '$TaskName'." -ForegroundColor Green
} else {
    Write-Host "'$TaskName' isn't registered — nothing to do."
}
