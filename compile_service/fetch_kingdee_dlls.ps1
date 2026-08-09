# fetch_kingdee_dlls.ps1
# 从金蝶云星空服务器 WebSite\bin 拷贝核心 BOS DLL 到 compile_service\build\references。
# 用途:Windows 部署编译服务前的 DLL 准备步骤(部署手册: docs/kingdee-plugin-agent/windows-deployment.md)。
#
# 注意:金蝶 BOS DLL 是商业闭源软件。本脚本仅从本机/内网金蝶服务器拷贝,
# 不下载、不转发、不公开分发;使用需确认团队金蝶授权合规。
#
# 用法(管理员 PowerShell):
#   powershell -ExecutionPolicy Bypass -File .\fetch_kingdee_dlls.ps1
# 可选参数:
#   -SourceDir "E:\Program Files (x86)\Kingdee\K3Cloud\WebSite\bin"   # 指定金蝶 bin 目录(跳过自动探测)
#   -TargetDir "E:\agentstore\compile_service\build\references"        # 目标目录(默认当前仓库同路径)
#   -All          # 拷贝全部 Kingdee.*.dll(默认只拷核心 4 + 常用补充)

param(
    [string]$SourceDir = "",
    [string]$TargetDir = "",
    [switch]$All
)

$ErrorActionPreference = "Stop"

# 核心 DLL(编译金蝶插件的最小引用集)
$coreDlls = @(
    "Kingdee.BOS.dll",
    "Kingdee.BOS.Core.dll",
    "Kingdee.BOS.App.dll",
    "Kingdee.K3.Core.dll"
)

# 常用补充(事件参数/元数据等,缺引用报错时按需加)
$extraDlls = @(
    "Kingdee.BOS.App.DataEntity.dll",
    "Kingdee.BOS.DataEntity.dll",
    "Kingdee.BOS.Security.dll",
    "Kingdee.BOS.ServiceFacade.Common.dll"
)

# 自动探测金蝶 WebSite\bin(常见安装位置)
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
            Write-Host "探测到金蝶 bin 目录: $SourceDir"
            break
        }
    }
}
if (-not $SourceDir -or -not (Test-Path $SourceDir)) {
    Write-Host "错误:未找到金蝶 WebSite\bin(可用 -SourceDir 显式指定)" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $SourceDir "Kingdee.BOS.dll"))) {
    Write-Host "错误:目录中不存在 Kingdee.BOS.dll,确认是金蝶 WebSite\bin: $SourceDir" -ForegroundColor Red
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

Write-Host "完成:拷贝 $copied 个 DLL 到 $TargetDir"
if ($missing.Count -gt 0) {
    Write-Host "缺失(可忽略,按需补充): $($missing -join ', ')" -ForegroundColor Yellow
}
Write-Host "下一步:按部署手册启动编译服务(health 验证 + 真实编译测试)。"
