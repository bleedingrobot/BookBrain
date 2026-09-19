# Starts OpenBooks in server mode for the BookBrain "Find a book" flow.
#
# BookBrain connects to this as the SINGLE allowed WebSocket client, so don't
# open the OpenBooks web UI (http://localhost:5228) while BookBrain is using it
# - the server refuses a second connection.
#
# openbooks.exe is gitignored; download it once from
#   https://github.com/evan-buss/openbooks/releases  (openbooks.exe asset)
# into this folder.
#
# Keep OPENBOOKS_DOWNLOAD_DIR in backend/.env matching -d below.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $here "openbooks.exe"
$dir = Join-Path $here "openbooks-dl"

if (-not (Test-Path $exe)) {
    Write-Error "openbooks.exe not found in $here - download it from https://github.com/evan-buss/openbooks/releases"
}

New-Item -ItemType Directory -Force -Path $dir | Out-Null

& $exe server `
    --port 5228 `
    --persist `
    --dir $dir `
    --no-browser-downloads `
    --searchbot search `
    --name ("bb_" + (Get-Random -Maximum 99999))
