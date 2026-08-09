# fetch_kingdee_dlls.ps1
# Copy core BOS DLLs from a Kingdee server WebSite\bin to compile_service\build\references.
# Purpose: prepare DLLs before deploying the compile service on Windows (see docs/kingdee-plugin-agent/windows-deployment.md).
#
# NOTE: Kingdee BOS DLLs are commercial closed-source software. This script only copies
# from a local/intranet Kingdee server; it never downloads, redistributes or publishes them. Ensure your team has proper Kingdee licensing.
#
# Usage (admin PowerShell):
#   powershell -ExecutionPolicy Bypass -File .\fetch_kingdee_dlls.ps1
# Options:
#   -SourceDir "E:\Program Files (x86)\Kingdee\K3Cloud\WebSite\bin"   # specify Kingdee bin dir (skip auto-detect)
#   -TargetDir "E:\agentstore\compile_service\build\references"        # target dir (default: repo\build\references)
#   -All          # copy ALL Kingdee.*.dll (default: core 4 + common extras)

param(
    [string]$SourceDir = "",
    [string]$TargetDir = "",
    [switch]$All
)

$ErrorActionPreference = "Stop"

# Core DLLs (minimal reference set for Kingdee plugin compilation)
$coreDlls = @(
    "Kingdee.BOS.dll",
    "Kingdee.BOS.Core.dll",
    "Kingdee.BOS.App.dll",
    "Kingdee.K3.Core.dll"
)

# Common extras (event args/metadata etc; add more if references are missing)
$extraDlls = @(
    "Kingdee.BOS.App.DataEntity.dll",
    "Kingdee.BOS.DataEntity.dll",
    "Kingdee.BOS.Security.dll",
    "Kingdee.BOS.ServiceFacade.Common.dll"
)

# Auto-detect Kingdee WebSite\bin (common install locations)
$candidates = @(
    "E:\Program Files (x86)\Kingdee\K3Cloud\WebSite\bin",
    "D:\Program Files (x86)\Kingdee\K3Cloud\WebSite\bin",
    "C:\Program Files (x86)\Kingdee\K3Cloud\WebSite\bin",
    "C:\Program Files\Kingdee\K3Cloud\WebSite\bin"
)

if (-not $SourceDir) {
    foreach ($c in $candidates) {
        if (Test-Path (Join-Path $c "Kingdee.BOS.dll")) {
            $SourceDir = $c
            Write-Host "Detected Kingdee bin dir: $SourceDir"
            break
        }
    }
}
if (-not $SourceDir -or -not (Test-Path $SourceDir)) {
    Write-Host "ERROR: Kingdee WebSite\bin not found (use -SourceDir to specify)" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $SourceDir "Kingdee.BOS.dll"))) {
    Write-Host "ERROR: Kingdee.BOS.dll not found in dir, confirm it is the Kingdee WebSite\bin: $SourceDir" -ForegroundColor Red
    exit 1
}

if (-not $TargetDir) {
    $repo = Split-Path -Parent $PSScriptRoot
    $TargetDir = Join-Path $repo "build\references"
}
New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

$toCopy = @($coreDlls) + $(if ($All) { Get-ChildItem $SourceDir -Filter "Kingdee.*.dll" | ForEach-Object { $_.Name } } else { $extraDlls })
$copied = 0
$missing = @()
foreach ($dll in ($toCopy | Select-Object -Unique)) {
    $src = Join-Path $SourceDir $dll
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $TargetDir $dll) -Force
        $copied++
    } else {
        $missing += $dll
    }
}

Write-Host "Done: copied $copied DLLs to $TargetDir"
if ($missing.Count -gt 0) {
    Write-Host "Missing (optional, add as needed): $($missing -join ', ')" -ForegroundColor Yellow
}
Write-Host "Next: start the compile service per the deployment manual (health check + real compile test)."
