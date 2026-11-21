[CmdletBinding()]
param(
    [switch]$CpuOnly,
    [switch]$Dev,
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cacheRoot = Join-Path $projectRoot '.cache'
$cacheEnvironment = [ordered]@{
    UV_CACHE_DIR = Join-Path $cacheRoot 'uv'
    UV_PYTHON_INSTALL_DIR = Join-Path $cacheRoot 'python'
    UV_PYTHON_BIN_DIR = Join-Path $cacheRoot 'python-bin'
    PIP_CACHE_DIR = Join-Path $cacheRoot 'pip'
    HF_HOME = Join-Path $cacheRoot 'huggingface'
    TORCH_HOME = Join-Path $cacheRoot 'torch'
    TEMP = Join-Path $cacheRoot 'tmp'
    TMP = Join-Path $cacheRoot 'tmp'
    TMPDIR = Join-Path $cacheRoot 'tmp'
    UV_PROJECT_ENVIRONMENT = Join-Path $projectRoot '.venv'
}
$previousEnvironment = @{}
$syncArguments = @('sync', '--locked', '--python', '3.12')
if (-not $CpuOnly) { $syncArguments += @('--extra', 'gpu') }
if ($Dev) { $syncArguments += @('--extra', 'dev') }

Push-Location -LiteralPath $projectRoot
try {
    foreach ($name in $cacheEnvironment.Keys) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $cacheEnvironment[$name], 'Process')
    }
    # Read-only validation: no directories, downloads, installs, or app launch.
    if ($Check) {
        [pscustomobject]@{
            project = $projectRoot
            environment = $cacheEnvironment
            command = @('uv') + $syncArguments
        } | ConvertTo-Json -Depth 4
        return
    }
    $uvCommand = Get-Command uv -CommandType Application -ErrorAction Stop
    foreach ($name in $cacheEnvironment.Keys) {
        if ($name -eq 'UV_PROJECT_ENVIRONMENT') { continue }
        $directory = $cacheEnvironment[$name]
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
        }
    }
    & $uvCommand.Source @syncArguments
    if ($LASTEXITCODE -ne 0) {
        throw "uv sync failed with exit code $LASTEXITCODE."
    }
}
finally {
    foreach ($name in $previousEnvironment.Keys) {
        if ($null -eq $previousEnvironment[$name]) {
            [Environment]::SetEnvironmentVariable($name, [NullString]::Value, 'Process')
        }
        else {
            [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
        }
    }
    Pop-Location
}
