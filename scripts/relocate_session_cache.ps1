# One-time, manifest-scoped relocation of this session's uv cache archives.
# Copy + SHA-256 verification precedes removal; junctions preserve uv references.
param([Parameter(Mandatory=$true)][string]$Manifest)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$sourceRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'uv/cache/archive-v0'))
$destinationRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot '.cache/relocated-uv/archive-v0'))
if (-not $destinationRoot.StartsWith($projectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Destination outside project.' }
New-Item -ItemType Directory -Path $destinationRoot -Force | Out-Null
$rows = Get-Content -LiteralPath $Manifest -Raw | ConvertFrom-Json
$results = @()
$freeBefore = (Get-PSDrive C).Free
foreach ($row in $rows) {
    if ($row.name -notmatch '^[A-Za-z0-9_-]+$') { throw 'Invalid archive name.' }
    $source = [IO.Path]::GetFullPath((Join-Path $sourceRoot $row.name))
    $destination = [IO.Path]::GetFullPath((Join-Path $destinationRoot $row.name))
    if ((Split-Path -Parent $source) -ne $sourceRoot -or (Split-Path -Parent $destination) -ne $destinationRoot) { throw 'Invalid resolved target.' }
    $item = Get-Item -LiteralPath $source
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Already relocated: $source" }
    if (Test-Path -LiteralPath $destination) { throw "Destination already exists: $destination" }
    if ($item.CreationTime -lt [datetime]'2026-11-14T00:30:00') { throw 'Archive predates this task.' }
    $files = @(Get-ChildItem -LiteralPath $source -Recurse -File -Force)
    $total = ($files | Measure-Object -Property Length -Sum).Sum
    if ($total -ne $row.bytes) { throw 'Archive changed after inventory.' }
    Write-Output "Copying $($row.name): $([math]::Round($total / 1MB, 1)) MiB"
    Copy-Item -LiteralPath $source -Destination $destination -Recurse
    $copied = @(Get-ChildItem -LiteralPath $destination -Recurse -File -Force)
    if ($copied.Count -ne $files.Count) { throw 'Copied file count mismatch.' }
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($source.Length).TrimStart([IO.Path]::DirectorySeparatorChar)
        $copy = Join-Path $destination $relative
        if (-not (Test-Path -LiteralPath $copy)) { throw 'Missing copied file.' }
        if ((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $copy -Algorithm SHA256).Hash) { throw 'Copied file hash mismatch.' }
    }
    # Both fully resolved absolute targets were checked against their fixed roots above.
    Remove-Item -LiteralPath $source -Recurse -Force
    New-Item -ItemType Junction -Path $source -Target $destination | Out-Null
    $results += [pscustomobject]@{name=$row.name; bytes=$total; files=$files.Count; sha256_verified=$true; junction=$true}
    $results | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $projectRoot 'reports/private/cache-relocation-progress.json') -Encoding utf8
}
$summary = [pscustomobject]@{archives=$results.Count; logical_bytes=($results | Measure-Object bytes -Sum).Sum; c_free_before=$freeBefore; c_free_after=(Get-PSDrive C).Free; entries=$results}
$summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $projectRoot 'reports/private/cache-relocation.json') -Encoding utf8
Write-Output "Relocated $($results.Count) archives; measured C free-space gain $([math]::Round(($summary.c_free_after-$freeBefore)/1GB,3)) GiB."
