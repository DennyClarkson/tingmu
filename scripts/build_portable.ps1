param([switch]$RefreshApp, [switch]$ImportUserData)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv/Scripts/python.exe'
$builder = Join-Path $projectRoot 'packaging/build_portable.py'
$arguments = @($builder)
if ($RefreshApp) { $arguments += '--refresh-app' }
if ($ImportUserData) { $arguments += '--import-user-data' }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw "Portable build failed ($LASTEXITCODE)" }
