param(
    [Parameter(Mandatory)][string]$Root,
    [switch]$PrepareOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$utf8 = New-Object System.Text.UTF8Encoding($false)
try {
    [Console]::InputEncoding = $utf8
    [Console]::OutputEncoding = $utf8
    $OutputEncoding = $utf8
}
catch {
    # Some non-interactive hosts do not expose a console encoding.
}

# start.bat 已切换代码页；这里再确认一次，避免中文在部分终端被拆成双字节逐个回显
# （旧日志里出现「正正在在尝试试」就是这种重复写入）。
try {
    $previousCodePage = (chcp) 2>$null
    if ($null -eq $previousCodePage -or $previousCodePage -notmatch "65001") {
        & chcp.com 65001 *> $null
    }
}
catch {
    # 不是控制台宿主时忽略。
}

$modulePath = Join-Path $PSScriptRoot "windows_bootstrap\Bootstrap.psm1"
$installLog = Join-Path $Root ".runtime\bootstrap\python-install.log"
$bootstrapLog = Join-Path $Root ".runtime\bootstrap\bootstrap.log"

try {
    Import-Module $modulePath -Force
    $exitCode = Invoke-AnythingMindmapBootstrap -Root $Root -PrepareOnly:$PrepareOnly
    exit $exitCode
}
catch {
    $failureMessage = $_.Exception.Message
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logEntry = @(
        ""
        "[$timestamp] 环境准备失败，AnythingMindmap 无法启动。"
        "具体原因：$failureMessage"
        "常见原因：网络或代理不可用、项目目录没有写入权限、Python 安装包被安全软件拦截，或依赖安装失败。"
        "处理建议：先确认项目文件夹已完整解压且不在只读位置，再重新双击 start.bat。"
    )
    try {
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $bootstrapLog) | Out-Null
        Add-Content -LiteralPath $bootstrapLog -Value $logEntry -Encoding UTF8
    }
    catch {
        Write-Host "警告：无法写入启动日志：$bootstrapLog" -ForegroundColor Yellow
    }

    # 把失败落在具体步骤上，用户一眼能看出卡在哪。
    try {
        $module = Get-Module Bootstrap
        if ($null -ne $module) {
            & $module { Fail-ActiveInstallStep }
        }
    }
    catch {
    }

    Write-Host ""
    Write-Host "  环境准备失败，AnythingMindmap 无法启动。" -ForegroundColor Red
    Write-Host "  具体原因：$failureMessage"
    Write-Host "  常见原因：网络或代理不可用、项目目录没有写入权限、Python 安装包被安全软件拦截，或依赖安装失败。"
    if (Test-Path -LiteralPath $installLog -PathType Leaf) {
        Write-Host "  Python 安装日志：$installLog"
    }
    Write-Host "  启动日志：$bootstrapLog"
    Write-Host "  处理建议：先确认项目文件夹已完整解压且不在只读位置，再重新双击 start.bat。"
    exit 1
}
