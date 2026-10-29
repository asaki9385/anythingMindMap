Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:PythonVersion = "3.12.10"
$script:PythonInstallerSha256 = "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb"
$script:EmbeddedPythonSha256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
$script:PipBootstrapVersion = "25.2"
$script:PipBootstrapSha256 = "6d67a2b4e7f14d8b31b8b52648866fa717f45a1eb70e83002f4331d07e953717"
$script:PipBootstrapPath = "packages/b7/3f/945ef7ab14dc4f9d7f40288d2df998d1837ee0888ec3659c813487572faa/pip-$script:PipBootstrapVersion-py3-none-any.whl"
$script:RequiredImportsScript = "import fastapi, uvicorn, pymupdf, httpx, requests, multipart, docx, chardet"
# 比导入检查更深一层：真正拉起应用模块（含 parser/ 与 mineru_adapter/ 子包）。
$script:ServerImportScript = "import sys; sys.path.insert(0, 'knowledge-compiler'); import server"

# ── 安装过程可视化 ──────────────────────────────────────────
# 只在“真终端”里画动画：输出被重定向到文件/管道时退化为纯文本，
# 避免把 \r 和转圈字符写进日志。Windows PowerShell 5.1 也支持 \r 回写同一行。
$script:UseLiveProgress = $false
try {
    $script:UseLiveProgress = -not [Console]::IsOutputRedirected -and $env:NO_ANIMATION -ne "1"
}
catch {
    $script:UseLiveProgress = $false
}

$script:InstallSteps = @(
    @{ Key = "python";   Label = "检查 Python 环境" }
    @{ Key = "venv";     Label = "准备运行环境" }
    @{ Key = "deps";     Label = "安装项目依赖" }
    @{ Key = "verify";   Label = "校验安装结果" }
    @{ Key = "launch";   Label = "启动服务" }
)
$script:StepState = @{}        # key -> "pending" | "running" | "done" | "failed"
$script:StepStarted = @{}      # key -> [datetime]
$script:SpinnerFrames = @("|", "/", "-", "\")
$script:SpinnerIndex = 0
$script:OverallStart = $null

function Initialize-InstallProgress {
    $script:StepState = @{}
    $script:LastPlainState = @{}
    foreach ($step in $script:InstallSteps) {
        $script:StepState[$step.Key] = "pending"
        $script:LastPlainState[$step.Key] = ""
    }
    $script:OverallStart = Get-Date
}

function Get-InstallStepLabel {
    param([Parameter(Mandatory)][string]$Key)

    foreach ($step in $script:InstallSteps) {
        if ($step.Key -eq $Key) {
            return $step.Label
        }
    }
    return $Key
}

function Get-DisplayWidth {
    # 中日韩字符在控制台占两格，用字符数排版会错位。
    param([string]$Text)

    $width = 0
    foreach ($ch in $Text.ToCharArray()) {
        $code = [int]$ch
        if (
            ($code -ge 0x1100 -and $code -le 0x115F) -or
            ($code -ge 0x2E80 -and $code -le 0xA4CF) -or
            ($code -ge 0xAC00 -and $code -le 0xD7A3) -or
            ($code -ge 0xF900 -and $code -le 0xFAFF) -or
            ($code -ge 0xFE30 -and $code -le 0xFE6F) -or
            ($code -ge 0xFF00 -and $code -le 0xFF60) -or
            ($code -ge 0xFFE0 -and $code -le 0xFFE6)
        ) {
            $width += 2
        }
        else {
            $width += 1
        }
    }
    return $width
}

function Write-InstallBanner {
    param([string]$Title = "AnythingMindmap 环境安装")

    $innerWidth = 44
    $padding = $innerWidth - (Get-DisplayWidth $Title)
    if ($padding -lt 0) { $padding = 0 }
    $line = "  │  " + $Title + (" " * $padding) + "│"

    Write-Host ""
    Write-Host ("  ┌" + ("─" * ($innerWidth + 2)) + "┐") -ForegroundColor DarkCyan
    Write-Host $line -ForegroundColor Cyan
    Write-Host ("  └" + ("─" * ($innerWidth + 2)) + "┘") -ForegroundColor DarkCyan
    Write-Host ""
}

function Get-InstallProgressLines {
    <#
      生成当前画面（不输出）。拆出来是为了可测试：
      画面内容与实际绘制分离，能直接断言每一步的标记与耗时。
    #>
    param([string]$Detail = "")

    $lines = @()
    $total = $script:InstallSteps.Count
    for ($i = 0; $i -lt $total; $i++) {
        $step = $script:InstallSteps[$i]
        $state = $script:StepState[$step.Key]
        $number = "[{0}/{1}]" -f ($i + 1), $total

        switch ($state) {
            "done"    { $mark = "√"; $color = "Green" }
            "failed"  { $mark = "×"; $color = "Red" }
            "running" { $mark = $script:SpinnerFrames[$script:SpinnerIndex % $script:SpinnerFrames.Count]; $color = "Cyan" }
            default   { $mark = "·"; $color = "DarkGray" }
        }

        $elapsed = ""
        if ($script:StepStarted.ContainsKey($step.Key) -and $state -eq "running") {
            $seconds = [int]((Get-Date) - $script:StepStarted[$step.Key]).TotalSeconds
            $elapsed = "  ({0}s)" -f $seconds
        }

        $line = "  $number $mark $($step.Label)$elapsed"
        if ($state -eq "running" -and -not [string]::IsNullOrWhiteSpace($Detail)) {
            $line += "  $Detail"
        }
        $lines += ,@($line, $color)
    }
    return $lines
}

function Write-InstallProgress {
    <#
      渲染全部步骤的当前状态。UseLiveProgress 时整块原地刷新，
      否则每条状态只打印一次（重定向到日志时清楚可读）。
    #>
    param(
        [string]$ActiveKey,
        [string]$Detail = "",
        [switch]$Force
    )

    if ($null -eq $script:OverallStart) {
        Initialize-InstallProgress
    }

    $lines = Get-InstallProgressLines -Detail $Detail
    $script:SpinnerIndex++

    if ($script:UseLiveProgress) {
        # 回到本块第一行并逐行覆盖。用 [char]27 而不是 `e：
        # `e 是 PowerShell 6+ 语法，Windows PowerShell 5.1 上会解析失败。
        $esc = [string][char]27
        $count = $lines.Count
        [Console]::Write($esc + "[{0}A" -f $count)
        foreach ($entry in $lines) {
            # 2K = 清整行，避免新内容比旧内容短时留下尾巴。
            [Console]::Write($esc + "[2K" + $entry[0] + "`n")
        }
    }
    else {
        # 非交互输出（重定向到日志）：只打印真正变化的那一步，避免把整块重复写进日志。
        # running 不写、pending 不写，日志里就是「一步一行结果」。
        for ($i = 0; $i -lt $lines.Count; $i++) {
            $key = $script:InstallSteps[$i].Key
            $state = $script:StepState[$key]
            if ($script:LastPlainState[$key] -eq $state) {
                continue
            }
            $script:LastPlainState[$key] = $state
            if ($state -eq "pending" -or $state -eq "running") {
                continue
            }
            Write-Host $lines[$i][0] -ForegroundColor $lines[$i][1]
        }
    }
}

function Start-InstallStep {
    param([Parameter(Mandatory)][string]$Key)

    $script:StepState[$Key] = "running"
    $script:StepStarted[$Key] = Get-Date
    Write-InstallProgress -ActiveKey $Key
}

function Complete-InstallStep {
    param([Parameter(Mandatory)][string]$Key)

    $script:StepState[$Key] = "done"
    Write-InstallProgress
}

function Fail-InstallStep {
    param([Parameter(Mandatory)][string]$Key)

    $script:StepState[$Key] = "failed"
    Write-InstallProgress
}

function Fail-ActiveInstallStep {
    # 供外层 catch 使用：把当前处于 running 的步骤标红，其余保持原状。
    if ($null -eq $script:StepState -or $script:StepState.Count -eq 0) {
        return
    }
    $active = $null
    foreach ($step in $script:InstallSteps) {
        if ($script:StepState.ContainsKey($step.Key) -and $script:StepState[$step.Key] -eq "running") {
            $active = $step.Key
            break
        }
    }
    if ($null -ne $active) {
        $script:StepState[$active] = "failed"
    }
    Write-InstallProgress
}

function Write-InstallSummary {
    if ($null -eq $script:OverallStart) {
        return
    }
    $elapsed = [int]((Get-Date) - $script:OverallStart).TotalSeconds
    $minutes = [int]($elapsed / 60)
    $seconds = $elapsed % 60
    $duration = if ($minutes -gt 0) { "${minutes} 分 ${seconds} 秒" } else { "${seconds} 秒" }
    Write-Host ""
    Write-Host "  安装完成，用时 $duration" -ForegroundColor Green
    Write-Host ""
}

function Invoke-NativeCommand {    param(
        [Parameter(Mandatory)][string]$FilePath,
        [AllowEmptyCollection()][string[]]$Arguments = @(),
        [string]$WorkingDirectory
    )

    # Windows PowerShell 5.1 在 $ErrorActionPreference = "Stop" 时，会把本地程序的
    # stderr 每一行包装成 NativeCommandError 并终止脚本。Python 的 warning、pip 的
    # 提示、uvicorn 的访问日志都写 stderr，按 stderr 判失败会误杀正常流程。
    # 因此这里只在调用期间放宽，按退出码判定成功与否。
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $pushedLocation = $false
    try {
        if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) {
            Push-Location -LiteralPath $WorkingDirectory
            $pushedLocation = $true
        }
        # 赋值本身不改变 $LASTEXITCODE，但先取再处理更保险。
        $output = @(& $FilePath @Arguments 2>&1 | ForEach-Object { [string]$_ })
        $exitCode = $LASTEXITCODE
        if ($null -eq $exitCode) {
            $exitCode = 0
        }
        return [pscustomobject]@{
            ExitCode = [int]$exitCode
            Output = $output
        }
    }
    finally {
        if ($pushedLocation) {
            Pop-Location
        }
        $ErrorActionPreference = $previousPreference
    }
}

function Invoke-NativeCommandStreaming {
    <#
      与 Invoke-NativeCommand 相同的 stderr/退出码语义，但边跑边回调，
      用于下载、安装这类长步骤。回调在同一线程执行（同一次轮询），
      不使用后台计时器，因而没有跨 runspace 的不确定性。
    #>
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [AllowEmptyCollection()][string[]]$Arguments = @(),
        [string]$WorkingDirectory,
        [scriptblock]$OnTick,
        [int]$TickMilliseconds = 250
    )

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $pushedLocation = $false
    $process = $null
    $errorLog = $null
    try {
        if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) {
            Push-Location -LiteralPath $WorkingDirectory
            $pushedLocation = $true
        }

        if ($null -eq $OnTick) {
            # 没有回调时退回逐行捕获，行为与 Invoke-NativeCommand 完全一致。
            $output = @(& $FilePath @Arguments 2>&1 | ForEach-Object { [string]$_ })
            $exitCode = $LASTEXITCODE
            if ($null -eq $exitCode) { $exitCode = 0 }
            return [pscustomobject]@{ ExitCode = [int]$exitCode; Output = $output }
        }

        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $FilePath
        $startInfo.UseShellExecute = $false
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.CreateNoWindow = $true
        $startInfo.WorkingDirectory = (Get-Location).Path
        # 用 ArgumentList 而不是拼 Arguments 字符串：后者在带空格/引号的参数上
        # 容易错位，且实测会让进程启动方式异常（轮询拿不到退出）。
        if ($null -ne $startInfo.PSObject.Properties["ArgumentList"]) {
            foreach ($argument in $Arguments) {
                [void]$startInfo.ArgumentList.Add([string]$argument)
            }
        }
        else {
            $quoted = @()
            foreach ($argument in $Arguments) {
                if ($argument -match '[\s"]') {
                    $quoted += '"' + ($argument -replace '"', '\"') + '"'
                }
                else {
                    $quoted += $argument
                }
            }
            $startInfo.Arguments = ($quoted -join " ")
        }

        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        [void]$process.Start()

        # 输出量大时管道写满会卡住子进程，必须并发读取。
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()

        while (-not $process.WaitForExit($TickMilliseconds)) {
            if ($null -ne $OnTick) {
                try {
                    & $OnTick
                }
                catch {
                }
            }
        }

        $stdout = $stdoutTask.Result
        $stderr = $stderrTask.Result
        $process.WaitForExit()

        $merged = @()
        if (-not [string]::IsNullOrEmpty($stdout)) {
            $merged += ($stdout -split "`r?`n")
        }
        if (-not [string]::IsNullOrEmpty($stderr)) {
            $merged += ($stderr -split "`r?`n")
        }
        $merged = @($merged | Where-Object { $_ -ne "" })

        return [pscustomobject]@{
            ExitCode = [int]$process.ExitCode
            Output = $merged
        }
    }
    finally {
        if ($null -ne $process) {
            $process.Dispose()
        }
        if ($pushedLocation) {
            Pop-Location
        }
        $ErrorActionPreference = $previousPreference
    }
}

function Get-PythonInstallerSource {
    param(
        [string]$Version = $script:PythonVersion
    )

    return @(
        [pscustomobject]@{
            Name = "华为云镜像"
            Url = "https://mirrors.huaweicloud.com/python/$Version/python-$Version-amd64.exe"
        }
        [pscustomobject]@{
            Name = "清华镜像"
            Url = "https://mirrors.tuna.tsinghua.edu.cn/python/$Version/python-$Version-amd64.exe"
        }
        [pscustomobject]@{
            Name = "阿里云镜像"
            Url = "https://mirrors.aliyun.com/python-release/windows/python-$Version-amd64.exe"
        }
        [pscustomobject]@{
            Name = "Python 官网"
            Url = "https://www.python.org/ftp/python/$Version/python-$Version-amd64.exe"
        }
    )
}

function Get-EmbeddedPythonSource {
    param(
        [string]$Version = $script:PythonVersion
    )

    return @(
        [pscustomobject]@{
            Name = "华为云镜像"
            Url = "https://mirrors.huaweicloud.com/python/$Version/python-$Version-embed-amd64.zip"
        }
        [pscustomobject]@{
            Name = "清华镜像"
            Url = "https://mirrors.tuna.tsinghua.edu.cn/python/$Version/python-$Version-embed-amd64.zip"
        }
        [pscustomobject]@{
            Name = "阿里云镜像"
            Url = "https://mirrors.aliyun.com/python-release/windows/python-$Version-embed-amd64.zip"
        }
        [pscustomobject]@{
            Name = "Python 官网"
            Url = "https://www.python.org/ftp/python/$Version/python-$Version-embed-amd64.zip"
        }
    )
}

function Get-PipBootstrapSource {
    return @(
        [pscustomobject]@{
            Name = "清华 PyPI 镜像"
            Url = "https://pypi.tuna.tsinghua.edu.cn/$script:PipBootstrapPath"
        }
        [pscustomobject]@{
            Name = "阿里云 PyPI 镜像"
            Url = "https://mirrors.aliyun.com/pypi/$script:PipBootstrapPath"
        }
        [pscustomobject]@{
            Name = "华为云 PyPI 镜像"
            Url = "https://mirrors.huaweicloud.com/repository/pypi/$script:PipBootstrapPath"
        }
        [pscustomobject]@{
            Name = "官方 PyPI"
            Url = "https://files.pythonhosted.org/$script:PipBootstrapPath"
        }
    )
}

function Get-PipIndexSource {
    return @(
        [pscustomobject]@{
            Name = "清华 PyPI 镜像"
            Url = "https://pypi.tuna.tsinghua.edu.cn/simple"
        }
        [pscustomobject]@{
            Name = "阿里云 PyPI 镜像"
            Url = "https://mirrors.aliyun.com/pypi/simple/"
        }
        [pscustomobject]@{
            Name = "华为云 PyPI 镜像"
            Url = "https://mirrors.huaweicloud.com/repository/pypi/simple"
        }
        [pscustomobject]@{
            Name = "官方 PyPI"
            Url = "https://pypi.org/simple"
        }
    )
}

function Invoke-MirrorAction {
    param(
        [AllowEmptyCollection()][object[]]$Source = @(),
        [Parameter(Mandatory)][string]$OperationName,
        [Parameter(Mandatory)][scriptblock]$Action,
        [int]$AttemptsPerSource = 2
    )

    $failures = @()
    foreach ($currentSource in $Source) {
        for ($attempt = 1; $attempt -le $AttemptsPerSource; $attempt++) {
            if ($attempt -eq 1) {
                Write-Host "正在尝试 $($currentSource.Name)..."
            }
            else {
                Write-Host "正在重试 $($currentSource.Name)（第 $attempt 次）..."
            }
            try {
                $result = & $Action $currentSource
                if ($null -ne $result) {
                    return $result
                }
                $failures += "$($currentSource.Name)：没有返回可用结果"
                Write-Host "从 $($currentSource.Name) ${OperationName}没有得到可用结果。"
            }
            catch {
                $message = $_.Exception.Message
                $failures += "$($currentSource.Name)：$message"
                Write-Host "从 $($currentSource.Name) ${OperationName}失败：$message"
            }
        }
        Write-Host "已跳过 $($currentSource.Name)，正在尝试下一个源..."
    }

    $detail = $failures -join "；"
    throw "所有镜像源均失败，无法${OperationName}。可能原因包括：网络断开、代理设置不正确、镜像暂时不可用，或安全软件拦截下载。请检查网络或代理后重试。详细信息：$detail"
}

function Find-PythonInterpreter {
    param(
        [AllowEmptyCollection()][object[]]$Candidates = @(),
        [version]$MinimumVersion = [version]"3.10"
    )

    foreach ($candidate in $Candidates) {
        if ($null -eq $candidate -or [string]::IsNullOrWhiteSpace([string]$candidate.Executable)) {
            continue
        }

        $prefixArgs = @()
        if ($null -ne $candidate.PrefixArgs) {
            $prefixArgs = @($candidate.PrefixArgs)
        }

        try {
            $versionResult = Invoke-NativeCommand -FilePath $candidate.Executable `
                -Arguments (@($prefixArgs) + @("-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"))
            if ($versionResult.ExitCode -ne 0) {
                continue
            }

            $versionText = [string]($versionResult.Output | Select-Object -Last 1)
            if ([string]::IsNullOrWhiteSpace($versionText)) {
                continue
            }
            $version = [version]$versionText.Trim()
            if ($version -ge $MinimumVersion) {
                $directEnvironment = $false
                $directProperty = $candidate.PSObject.Properties["DirectEnvironment"]
                if ($null -ne $directProperty) {
                    $directEnvironment = [bool]$directProperty.Value
                }
                return [pscustomobject]@{
                    Executable = [string]$candidate.Executable
                    PrefixArgs = $prefixArgs
                    Version = $version
                    DirectEnvironment = $directEnvironment
                }
            }
        }
        catch {
            continue
        }
    }

    return $null
}

function Test-PythonHasVenv {
    param(
        [Parameter(Mandatory)][pscustomobject]$Python
    )

    $result = Invoke-NativeCommand -FilePath $Python.Executable `
        -Arguments (@($Python.PrefixArgs) + @("-c", "import venv, ensurepip"))
    return ($result.ExitCode -eq 0)
}

function Get-InstalledPythonCandidate {
    $paths = [System.Collections.Generic.List[string]]::new()
    $searchRoots = @()

    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $searchRoots += Join-Path $env:LOCALAPPDATA "Programs\Python"
    }
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $searchRoots += $env:ProgramFiles
    }
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $searchRoots += ${env:ProgramFiles(x86)}
    }

    foreach ($searchRoot in $searchRoots) {
        if (-not (Test-Path -LiteralPath $searchRoot -PathType Container)) {
            continue
        }

        $directories = Get-ChildItem `
            -LiteralPath $searchRoot `
            -Directory `
            -Filter "Python3*" `
            -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending
        foreach ($directory in $directories) {
            $pythonPath = Join-Path $directory.FullName "python.exe"
            if ((Test-Path -LiteralPath $pythonPath -PathType Leaf) -and -not $paths.Contains($pythonPath)) {
                $paths.Add($pythonPath)
            }
        }
    }

    $registryRoots = @(
        "HKCU:\Software\Python\PythonCore"
        "HKLM:\SOFTWARE\Python\PythonCore"
        "HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore"
    )
    foreach ($registryRoot in $registryRoots) {
        if (-not (Test-Path -LiteralPath $registryRoot)) {
            continue
        }

        $versions = Get-ChildItem -LiteralPath $registryRoot -ErrorAction SilentlyContinue |
            Sort-Object PSChildName -Descending
        foreach ($versionKey in $versions) {
            $installKey = Join-Path $versionKey.PSPath "InstallPath"
            if (-not (Test-Path -LiteralPath $installKey)) {
                continue
            }

            try {
                $properties = Get-ItemProperty -LiteralPath $installKey
                $executablePath = [string]$properties.ExecutablePath
                if (-not [string]::IsNullOrWhiteSpace($executablePath)) {
                    $pythonPath = $executablePath
                }
                else {
                    $defaultProperty = $properties.PSObject.Properties["(default)"]
                    if ($null -eq $defaultProperty) {
                        continue
                    }
                    $installDirectory = [string]$defaultProperty.Value
                    if ([string]::IsNullOrWhiteSpace($installDirectory)) {
                        continue
                    }
                    $pythonPath = Join-Path $installDirectory "python.exe"
                }
            }
            catch {
                continue
            }

            if (
                -not [string]::IsNullOrWhiteSpace($pythonPath) -and
                (Test-Path -LiteralPath $pythonPath -PathType Leaf) -and
                -not $paths.Contains($pythonPath)
            ) {
                $paths.Add($pythonPath)
            }
        }
    }

    return @(
        $paths | ForEach-Object {
            [pscustomobject]@{
                Executable = $_
                PrefixArgs = @()
            }
        }
    )
}

function Get-PythonCandidates {
    param(
        [Parameter(Mandatory)][string]$Root,
        [switch]$ForceLocalPython
    )

    $candidates = @()
    if (-not $ForceLocalPython -and -not [string]::IsNullOrWhiteSpace($env:MINDMAP_PYTHON)) {
        $configuredPython = $env:MINDMAP_PYTHON.Trim().Trim('"')
        if (Test-Path -LiteralPath $configuredPython -PathType Leaf) {
            $candidates += [pscustomobject]@{
                Executable = $configuredPython
                PrefixArgs = @()
            }
        }
    }

    $localPython = Join-Path $Root ".runtime\python\python.exe"
    if (Test-Path -LiteralPath $localPython -PathType Leaf) {
        $embeddedMarker = Join-Path $Root ".runtime\python\.mindmap-embedded"
        $candidates += [pscustomobject]@{
            Executable = $localPython
            PrefixArgs = @()
            DirectEnvironment = Test-Path -LiteralPath $embeddedMarker -PathType Leaf
        }
    }

    if (-not $ForceLocalPython) {
        $candidates += @(Get-InstalledPythonCandidate)
    }

    if (-not $ForceLocalPython) {
        # py.exe / python.exe 可能是应用商店占位符或精简安装，是否可用由 Find-PythonInterpreter 实测决定。
        $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $pyLauncher) {
            $candidates += [pscustomobject]@{
                Executable = $pyLauncher.Source
                PrefixArgs = @("-3")
            }
        }

        foreach ($commandName in @("python.exe", "python3.exe")) {
            $pythonCommand = Get-Command $commandName -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($null -ne $pythonCommand) {
                $candidates += [pscustomobject]@{
                    Executable = $pythonCommand.Source
                    PrefixArgs = @()
                }
            }
        }
    }

    return $candidates
}

function Initialize-EmbeddedPythonRuntime {
    param(
        [Parameter(Mandatory)][string]$PythonDirectory
    )

    $sitePackages = Join-Path $PythonDirectory "Lib\site-packages"
    New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null

    $pthFile = Get-ChildItem -LiteralPath $PythonDirectory -Filter "python*._pth" -File |
        Select-Object -First 1
    if ($null -eq $pthFile) {
        throw "嵌入式 Python 缺少 python*._pth 配置文件，无法启用依赖目录。"
    }

    $stdlibArchive = Get-ChildItem -LiteralPath $PythonDirectory -Filter "python*.zip" -File |
        Sort-Object Length -Descending |
        Select-Object -First 1
    if ($null -eq $stdlibArchive) {
        throw "嵌入式 Python 缺少标准库压缩包，无法继续安装依赖。"
    }

    $pthContent = @(
        $stdlibArchive.Name
        "."
        "Lib\site-packages"
        "import site"
        ""
    ) -join "`r`n"
    [System.IO.File]::WriteAllText($pthFile.FullName, $pthContent, [System.Text.Encoding]::ASCII)
}

function Get-PythonInstallerArguments {
    param(
        [Parameter(Mandatory)][string]$TargetDirectory,
        [Parameter(Mandatory)][string]$LogPath
    )

    $quotedLogPath = '"' + $LogPath + '"'
    $quotedTargetDirectory = 'TargetDir="' + $TargetDirectory + '"'

    return @(
        "/quiet"
        "/log"
        $quotedLogPath
        "InstallAllUsers=0"
        "PrependPath=0"
        "Include_launcher=0"
        "InstallLauncherAllUsers=0"
        "Include_test=0"
        "Include_doc=0"
        "Include_tcltk=0"
        "Include_pip=1"
        "Include_dev=0"
        "Include_symbols=0"
        "Include_debug=0"
        "Shortcuts=0"
        "AssociateFiles=0"
        $quotedTargetDirectory
    )
}

function Get-SilentPythonInstallerArguments {
    param(
        [Parameter(Mandatory)][string]$TargetDirectory,
        [Parameter(Mandatory)][string]$LogPath
    )

    $quotedLogPath = '"' + $LogPath + '"'
    $quotedTargetDirectory = 'TargetDir="' + $TargetDirectory + '"'

    # 备用安装方式：不回滚、不写注册表、不要求包缓存，最容易在受限目录里成功。
    return @(
        "/quiet"
        "/passive"
        "/log"
        $quotedLogPath
        "InstallAllUsers=0"
        "PrependPath=0"
        "Include_launcher=0"
        "InstallLauncherAllUsers=0"
        "Include_test=0"
        "Include_doc=0"
        "Include_tcltk=0"
        "Include_pip=1"
        "Include_dev=0"
        "Include_symbols=0"
        "Include_debug=0"
        "Shortcuts=0"
        "AssociateFiles=0"
        "SimpleInstall=1"
        "CompileAll=0"
        $quotedTargetDirectory
    )
}

function Get-VerifiedPythonInstaller {
    param(
        [Parameter(Mandatory)][string]$InstallerPath
    )

    $installerDirectory = Split-Path -Parent $InstallerPath
    New-Item -ItemType Directory -Force -Path $installerDirectory | Out-Null

    $needsDownload = $true
    if (Test-Path -LiteralPath $InstallerPath -PathType Leaf) {
        $existingHash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $needsDownload = $existingHash -ne $script:PythonInstallerSha256
        if ($needsDownload) {
            # 校验不过的旧文件可能是半截下载或被安全软件改写的，删不掉也要继续重下。
            Remove-Item -LiteralPath $InstallerPath -Force -ErrorAction SilentlyContinue
        }
        else {
            Write-Host "使用已下载并通过校验的 Python 安装包。"
        }
    }

    if ($needsDownload) {
        Write-Host "未检测到可用的 Python，正在从国内镜像下载 Python $script:PythonVersion ..."
        $previousProgress = $ProgressPreference
        $expectedSha256 = $script:PythonInstallerSha256
        try {
            $ProgressPreference = "SilentlyContinue"
            # 同时放开 TLS 1.2/1.3：老系统默认可能只协商到 TLS 1.0，握手会被镜像拒绝。
            try {
                [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor `
                    [Net.SecurityProtocolType]::Tls12
            }
            catch {
                [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            }
            $downloadResult = Invoke-MirrorAction `
                -Source @(Get-PythonInstallerSource) `
                -OperationName "下载 Python 安装包" `
                -Action {
                    param($source)

                    if (Test-Path -LiteralPath $InstallerPath -PathType Leaf) {
                        Remove-Item -LiteralPath $InstallerPath -Force
                    }

                    try {
                        Invoke-WebRequest `
                            -Uri $source.Url `
                            -OutFile $InstallerPath `
                            -UseBasicParsing `
                            -TimeoutSec 300
                    }
                    catch {
                        Remove-Item -LiteralPath $InstallerPath -Force -ErrorAction SilentlyContinue
                        throw "下载失败：$($_.Exception.Message)"
                    }

                    try {
                        $actualHash = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash.ToLowerInvariant()
                    }
                    catch {
                        Remove-Item -LiteralPath $InstallerPath -Force -ErrorAction SilentlyContinue
                        throw "无法读取安装包进行校验：$($_.Exception.Message)"
                    }

                    if ($actualHash -ne $expectedSha256) {
                        Remove-Item -LiteralPath $InstallerPath -Force -ErrorAction SilentlyContinue
                        throw "安装包 SHA-256 校验失败，下载内容不是受信任的官方安装包。"
                    }

                    return [pscustomobject]@{
                        Path = $InstallerPath
                        SourceName = $source.Name
                    }
                }.GetNewClosure()

            Write-Host "已从 $($downloadResult.SourceName) 获取 Python 安装包。"
        }
        finally {
            $ProgressPreference = $previousProgress
        }
    }

    Unblock-File -LiteralPath $InstallerPath -ErrorAction SilentlyContinue
    return $InstallerPath
}

function Get-VerifiedDownloadArtifact {
    param(
        [Parameter(Mandatory)][string]$DownloadPath,
        [Parameter(Mandatory)][object[]]$Source,
        [Parameter(Mandatory)][string]$ExpectedSha256,
        [Parameter(Mandatory)][string]$Description
    )

    $downloadDirectory = Split-Path -Parent $DownloadPath
    New-Item -ItemType Directory -Force -Path $downloadDirectory | Out-Null
    $expectedHash = $ExpectedSha256.ToLowerInvariant()

    if (Test-Path -LiteralPath $DownloadPath -PathType Leaf) {
        $existingHash = (Get-FileHash -LiteralPath $DownloadPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($existingHash -eq $expectedHash) {
            Write-Host "使用已下载并通过校验的${Description}。"
            Unblock-File -LiteralPath $DownloadPath -ErrorAction SilentlyContinue
            return $DownloadPath
        }
        Remove-Item -LiteralPath $DownloadPath -Force -ErrorAction SilentlyContinue
    }

    $previousProgress = $ProgressPreference
    try {
        $ProgressPreference = "SilentlyContinue"
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor `
                [Net.SecurityProtocolType]::Tls12
        }
        catch {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        }
        $downloadResult = Invoke-MirrorAction `
            -Source $Source `
            -OperationName "下载${Description}" `
            -Action {
                param($currentSource)

                if (Test-Path -LiteralPath $DownloadPath -PathType Leaf) {
                    Remove-Item -LiteralPath $DownloadPath -Force -ErrorAction SilentlyContinue
                }

                try {
                    Invoke-WebRequest `
                        -Uri $currentSource.Url `
                        -OutFile $DownloadPath `
                        -UseBasicParsing `
                        -TimeoutSec 300
                }
                catch {
                    Remove-Item -LiteralPath $DownloadPath -Force -ErrorAction SilentlyContinue
                    throw "下载失败：$($_.Exception.Message)"
                }

                try {
                    $actualHash = (Get-FileHash -LiteralPath $DownloadPath -Algorithm SHA256).Hash.ToLowerInvariant()
                }
                catch {
                    Remove-Item -LiteralPath $DownloadPath -Force -ErrorAction SilentlyContinue
                    throw "无法读取下载文件进行校验：$($_.Exception.Message)"
                }

                if ($actualHash -ne $expectedHash) {
                    Remove-Item -LiteralPath $DownloadPath -Force -ErrorAction SilentlyContinue
                    throw "SHA-256 校验失败，下载内容不是受信任的文件。"
                }

                return [pscustomobject]@{
                    Path = $DownloadPath
                    SourceName = $currentSource.Name
                }
            }.GetNewClosure()

        Write-Host "已从 $($downloadResult.SourceName) 获取${Description}。"
    }
    finally {
        $ProgressPreference = $previousProgress
    }

    Unblock-File -LiteralPath $DownloadPath -ErrorAction SilentlyContinue
    return $DownloadPath
}

function Install-EmbeddedPython {
    param(
        [Parameter(Mandatory)][string]$Root
    )

    $runtimeRoot = Join-Path $Root ".runtime"
    $pythonDirectory = Join-Path $runtimeRoot "python"
    $bootstrapDirectory = Join-Path $runtimeRoot "bootstrap"
    $pythonExecutable = Join-Path $pythonDirectory "python.exe"
    $archivePath = Join-Path $bootstrapDirectory "python-$script:PythonVersion-embed-amd64.zip"
    $pipWheelPath = Join-Path $bootstrapDirectory "pip-$script:PipBootstrapVersion-py3-none-any.whl"
    $pipZipPath = Join-Path $bootstrapDirectory "pip-bootstrap.zip"
    $embeddedMarker = Join-Path $pythonDirectory ".mindmap-embedded"

    Write-Host "正在安装项目内嵌入式 Python $script:PythonVersion ..."
    try {
        $archivePath = Get-VerifiedDownloadArtifact `
            -DownloadPath $archivePath `
            -Source @(Get-EmbeddedPythonSource) `
            -ExpectedSha256 $script:EmbeddedPythonSha256 `
            -Description "嵌入式 Python 安装包"
        $pipWheelPath = Get-VerifiedDownloadArtifact `
            -DownloadPath $pipWheelPath `
            -Source @(Get-PipBootstrapSource) `
            -ExpectedSha256 $script:PipBootstrapSha256 `
            -Description "pip 引导文件"

        if (Test-Path -LiteralPath $pythonDirectory) {
            Remove-Item -LiteralPath $pythonDirectory -Recurse -Force
        }

        Expand-Archive -LiteralPath $archivePath -DestinationPath $pythonDirectory -Force
        Initialize-EmbeddedPythonRuntime -PythonDirectory $pythonDirectory

        $sitePackages = Join-Path $pythonDirectory "Lib\site-packages"
        Copy-Item -LiteralPath $pipWheelPath -Destination $pipZipPath -Force
        Expand-Archive -LiteralPath $pipZipPath -DestinationPath $sitePackages -Force
        Remove-Item -LiteralPath $pipZipPath -Force -ErrorAction SilentlyContinue
        Set-Content -LiteralPath $embeddedMarker -Value "embedded" -Encoding Ascii -NoNewline

        $embedded = Find-PythonInterpreter `
            -Candidates @(
                [pscustomobject]@{
                    Executable = $pythonExecutable
                    PrefixArgs = @()
                    DirectEnvironment = $true
                }
            ) `
            -MinimumVersion ([version]"3.10")
        if ($null -eq $embedded) {
            throw "嵌入式 Python 解压后无法运行。"
        }

        $embeddedPipCheck = Invoke-NativeCommand -FilePath $pythonExecutable -Arguments @("-m", "pip", "--version")
        if ($embeddedPipCheck.ExitCode -ne 0) {
            throw "嵌入式 Python 的 pip 初始化失败。"
        }

        Write-Host "项目内嵌入式 Python $($embedded.Version) 安装完成。"
        return $embedded
    }
    catch {
        Remove-Item -LiteralPath $pythonDirectory -Recurse -Force -ErrorAction SilentlyContinue
        throw "项目内嵌入式 Python 安装失败，可能原因：网络或代理不可用、目录没有写入权限，或安全软件拦截了解压和写入。详细信息：$($_.Exception.Message)"
    }
}

function Test-PythonInstallerNeedsFallback {
    param(
        [Parameter(Mandatory)][string]$LogPath,
        [Parameter(Mandatory)][string]$RuntimePython
    )

    if (Test-Path -LiteralPath $RuntimePython -PathType Leaf) {
        return $false
    }
    if (-not (Test-Path -LiteralPath $LogPath -PathType Leaf)) {
        return $false
    }

    try {
        $logText = Get-Content -LiteralPath $LogPath -Raw
    }
    catch {
        return $false
    }

    return (
        $logText -match "ActionLikeInstallation = Modify" -and
        $logText -match "WixBundleInstalled = 1"
    )
}

function Install-LocalPython {
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$LogPath,
        [switch]$ForceEmbedded
    )

    if (-not [Environment]::Is64BitOperatingSystem) {
        throw "自动安装需要 64 位 Windows。请换用 64 位系统，或手动安装 Python 3.10 及以上版本后重试。"
    }

    if ($ForceEmbedded) {
        return Install-EmbeddedPython -Root $Root
    }

    $runtimePython = Join-Path $Root ".runtime\python\python.exe"
    $runtimeDirectory = Split-Path -Parent $runtimePython
    $previousInstallWasModifyOnly = Test-PythonInstallerNeedsFallback -LogPath $LogPath -RuntimePython $runtimePython

    if ($previousInstallWasModifyOnly) {
        Write-Host "检测到上次 Windows Python 安装只执行了“修改现有安装”，但没有创建项目内 Python，正在直接改用项目内嵌入式 Python..."
        return Install-EmbeddedPython -Root $Root
    }

    $installerPath = Join-Path $Root ".runtime\bootstrap\python-$script:PythonVersion-amd64.exe"
    # 下载失败时 Get-VerifiedPythonInstaller 会抛出；这里接住，直接走嵌入式方案，
    # 不让一次下载失败把首次启动卡死。
    try {
        $installerPath = Get-VerifiedPythonInstaller -InstallerPath $installerPath
    }
    catch {
        Write-Host "下载 Python 安装包失败：$($_.Exception.Message)"
        Write-Host "正在改用项目内嵌入式 Python（只需要一个小得多的压缩包）..."
        return Install-EmbeddedPython -Root $Root
    }

    foreach ($attempt in @("normal", "simple")) {
        if (Test-Path -LiteralPath $runtimeDirectory) {
            Remove-Item -LiteralPath $runtimeDirectory -Recurse -Force -ErrorAction SilentlyContinue
        }

        if ($attempt -eq "normal") {
            Write-Host "正在将 Python $script:PythonVersion 安装到项目目录：$runtimeDirectory ..."
            $arguments = Get-PythonInstallerArguments -TargetDirectory $runtimeDirectory -LogPath $LogPath
        }
        else {
            Write-Host "第一次安装没有在项目目录留下可用的 Python，正在换用更保守的安装方式重试..."
            $arguments = Get-SilentPythonInstallerArguments -TargetDirectory $runtimeDirectory -LogPath $LogPath
        }

        $installerProcess = $null
        try {
            $installerProcess = Start-Process `
                -FilePath $installerPath `
                -ArgumentList $arguments `
                -Wait `
                -PassThru
        }
        catch {
            Write-Host "无法启动 Python 安装器：$($_.Exception.Message)"
        }

        if ($null -ne $installerProcess) {
            $installerExitCode = $installerProcess.ExitCode
            if ($installerExitCode -notin @(0, 3010)) {
                Write-Host "Python 安装器运行失败（退出代码 ${installerExitCode}）。"
            }
        }

        # 只要项目目录里出现了能跑的 Python，就算这次安装有效——不管安装器怎么报告。
        $installed = Find-PythonInterpreter `
            -Candidates @([pscustomobject]@{ Executable = $runtimePython; PrefixArgs = @() }) `
            -MinimumVersion ([version]"3.10")
        if ($null -ne $installed) {
            Write-Host "Python $($installed.Version) 安装完成：$($installed.Executable)"
            return $installed
        }
    }

    # 项目目录装不上（受限目录、安全软件、已注册的其它 Python 版本）就退回嵌入式 Python，
    # 它不需要写注册表，最不容易被拦。
    Write-Host "Windows 安装器没有在项目目录创建可用 Python，正在改用项目内嵌入式 Python..."
    if (Test-Path -LiteralPath $LogPath -PathType Leaf) {
        Write-Host "安装日志：$LogPath"
    }
    return Install-EmbeddedPython -Root $Root
}

function Ensure-ProjectVirtualEnvironment {
    param(
        [Parameter(Mandatory)][pscustomobject]$BasePython,
        [Parameter(Mandatory)][string]$VenvDirectory
    )

    $venvPython = Join-Path $VenvDirectory "Scripts\python.exe"
    $existing = Find-PythonInterpreter `
        -Candidates @([pscustomobject]@{ Executable = $venvPython; PrefixArgs = @() }) `
        -MinimumVersion ([version]"3.10")
    if ($null -ne $existing) {
        return $existing
    }

    if (Test-Path -LiteralPath $VenvDirectory) {
        Write-Host "项目虚拟环境不可用，正在重新创建..."
        Remove-Item -LiteralPath $VenvDirectory -Recurse -Force
    }
    else {
        Write-Host "正在创建项目虚拟环境..."
    }

    $venvArguments = @($BasePython.PrefixArgs) + @("-m", "venv", $VenvDirectory)
    $venvResult = Invoke-NativeCommand -FilePath $BasePython.Executable -Arguments $venvArguments
    foreach ($line in $venvResult.Output) { Write-Host $line }
    if ($venvResult.ExitCode -ne 0) {
        # 系统 Python 常见缺 venv（精简安装/精简运行库）。这里不直接报错，
        # 交给上层改用嵌入式 Python，避免用户卡在“缺组件”上。
        return $null
    }

    $created = Find-PythonInterpreter `
        -Candidates @([pscustomobject]@{ Executable = $venvPython; PrefixArgs = @() }) `
        -MinimumVersion ([version]"3.10")
    if ($null -eq $created) {
        return $null
    }

    return $created
}

function Test-DependenciesCurrent {
    param(
        [Parameter(Mandatory)][string]$RequirementsPath,
        [Parameter(Mandatory)][string]$StampPath,
        [Parameter(Mandatory)][scriptblock]$DependencyProbe
    )

    if (
        -not (Test-Path -LiteralPath $RequirementsPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $StampPath -PathType Leaf)
    ) {
        return $false
    }

    $requirementsHash = (Get-FileHash -LiteralPath $RequirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $recordedHash = (Get-Content -LiteralPath $StampPath -Raw).Trim().ToLowerInvariant()
    if ($requirementsHash -ne $recordedHash) {
        return $false
    }

    try {
        return [bool](& $DependencyProbe)
    }
    catch {
        return $false
    }
}

function Invoke-DependencySetup {
    param(
        [Parameter(Mandatory)][string]$PythonPath,
        [Parameter(Mandatory)][string]$RequirementsPath,
        [Parameter(Mandatory)][string]$StampPath
    )

    if (-not (Test-Path -LiteralPath $RequirementsPath -PathType Leaf)) {
        throw "项目缺少依赖清单 requirements.txt：$RequirementsPath"
    }

    $requiredImportsScript = $script:RequiredImportsScript
    $serverImportScript = $script:ServerImportScript
    $projectRoot = Split-Path -Parent $RequirementsPath
    # 先验依赖能否导入，再验应用模块能否导入——两者都过才算“已装好”，
    # 否则残缺/中断的安装会被 stamp 误判为最新。
    # 闭包用模块对象显式调用私有函数：GetNewClosure 的 scriptblock 在执行时
    # 未必还处在模块作用域内，直接写 Invoke-NativeCommand 会报“无法识别”。
    $module = $ExecutionContext.SessionState.Module
    if ($null -eq $module) {
        $module = Get-Module Bootstrap
    }
    $nativeInvoker = {
        param($path, $workingDirectory, $nativeArgs)
        if ([string]::IsNullOrWhiteSpace($workingDirectory)) {
            & $module { param($p, $a) Invoke-NativeCommand -FilePath $p -Arguments $a } $path $nativeArgs
        }
        else {
            & $module { param($p, $w, $a) Invoke-NativeCommand -FilePath $p -WorkingDirectory $w -Arguments $a } $path $workingDirectory $nativeArgs
        }
    }.GetNewClosure()

    $dependencyProbe = {
        $probe = & $nativeInvoker $PythonPath $projectRoot @("-c", $requiredImportsScript)
        if ($probe.ExitCode -ne 0) {
            return $false
        }
        $serverProbe = & $nativeInvoker $PythonPath $projectRoot @("-c", $serverImportScript)
        return ($serverProbe.ExitCode -eq 0)
    }.GetNewClosure()

    if (
        Test-DependenciesCurrent `
            -RequirementsPath $RequirementsPath `
            -StampPath $StampPath `
            -DependencyProbe $dependencyProbe
    ) {
        Write-Host "Python 依赖已是最新状态。"
        return
    }

    $pipCheck = & $nativeInvoker $PythonPath $null @("-m", "pip", "--version")
    if ($pipCheck.ExitCode -ne 0) {
        Write-Host "项目环境中缺少 pip，正在尝试自动修复..."
        $ensurePipResult = & $nativeInvoker $PythonPath $null @("-m", "ensurepip", "--upgrade")
        foreach ($line in $ensurePipResult.Output) { Write-Host $line }
        if ($ensurePipResult.ExitCode -ne 0) {
            throw "无法在项目虚拟环境中安装 pip。可能原因：项目目录不可写，或 Python 安装不完整。请检查目录权限和安全软件设置。"
        }
    }

    $installResult = Invoke-MirrorAction `
        -Source @(Get-PipIndexSource) `
        -OperationName "安装 Python 依赖" `
        -Action {
            param($source)

            Write-Host "正在从 $($source.Name) 安装 Python 依赖..."
            # pip 是首次启动最慢的一步；交互模式下边跑边刷新进度块。
            $pipTick = $null
            if ($script:UseLiveProgress) {
                $pipTick = { & $module { Write-InstallProgress -Detail "正在从 $($args[0]) 下载" } $source.Name }.GetNewClosure()
            }
            $pipResult = & $module {
                param($p, $w, $a, $tick)
                Invoke-NativeCommandStreaming -FilePath $p -WorkingDirectory $w -Arguments $a -OnTick $tick
            } $PythonPath $projectRoot @(
                "-m", "pip", "install",
                "--index-url", $source.Url,
                "--disable-pip-version-check",
                "--no-warn-script-location",
                "--timeout", "60",
                "--retries", "3",
                "--requirement", $RequirementsPath
            ) $pipTick
            foreach ($line in $pipResult.Output) { Write-Host $line }
            if ($pipResult.ExitCode -ne 0) {
                throw "pip 安装失败，退出代码：$($pipResult.ExitCode)"
            }

            return [pscustomobject]@{
                SourceName = $source.Name
                IndexUrl = $source.Url
            }
        }.GetNewClosure()

    Write-Host "Python 依赖已从 $($installResult.SourceName) 安装完成。"

    $importCheck = {
        param($script)
        & $nativeInvoker $PythonPath $projectRoot @("-c", $script)
    }.GetNewClosure()

    $probeResult = & $importCheck $requiredImportsScript
    if ($probeResult.ExitCode -ne 0) {
        # 第一次装完仍导入失败（多为残留的坏包/上次中断的安装），用同一镜像强制修复一次。
        Write-Host "依赖安装后导入检查未通过，正在尝试强制修复依赖..."
        $repairResult = & $nativeInvoker $PythonPath $projectRoot @(
            "-m", "pip", "install",
            "--index-url", $installResult.IndexUrl,
            "--disable-pip-version-check",
            "--no-warn-script-location",
            "--timeout", "60",
            "--retries", "3",
            "--force-reinstall",
            "--requirement", $RequirementsPath
        )
        foreach ($line in $repairResult.Output) { Write-Host $line }
        $probeResult = & $importCheck $requiredImportsScript
        if ($probeResult.ExitCode -ne 0) {
            foreach ($line in $probeResult.Output) { Write-Host $line }
            throw "依赖安装命令已完成，但应用导入检查失败。可能原因：依赖安装不完整、Python 版本不兼容，或安全软件阻止了文件写入。请删除 .runtime\venv 后重新双击 start.bat。"
        }
    }

    # 最后确认应用本身能被导入（覆盖 parser/、mineru_adapter/ 等子包和模块级初始化）。
    $serverProbe = & $importCheck $serverImportScript
    if ($serverProbe.ExitCode -ne 0) {
        foreach ($line in $serverProbe.Output) { Write-Host $line }
        throw "依赖已安装，但应用模块无法导入。可能原因：项目文件不完整（请重新解压完整项目文件夹），或依赖与当前 Python 版本不兼容。"
    }

    $stampDirectory = Split-Path -Parent $StampPath
    New-Item -ItemType Directory -Force -Path $stampDirectory | Out-Null
    $requirementsHash = (Get-FileHash -LiteralPath $RequirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath $StampPath -Value $requirementsHash -Encoding Ascii -NoNewline
    Write-Host "全部 Python 依赖安装成功。"
}

function Test-ProjectWritable {
    param(
        [Parameter(Mandatory)][string]$Root
    )

    $probePath = Join-Path $Root (".runtime\write-probe-" + [guid]::NewGuid().ToString("N") + ".tmp")
    try {
        $directory = Split-Path -Parent $probePath
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
        [System.IO.File]::WriteAllText($probePath, "probe", [System.Text.Encoding]::UTF8)
        Remove-Item -LiteralPath $probePath -Force -ErrorAction SilentlyContinue
        return $true
    }
    catch {
        return $false
    }
}

function Invoke-AnythingMindmapBootstrap {
    param(
        [Parameter(Mandatory)][string]$Root,
        [switch]$PrepareOnly
    )

    $resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
    $startScript = Join-Path $resolvedRoot "start.py"
    $requirementsPath = Join-Path $resolvedRoot "requirements.txt"
    $runtimeRoot = Join-Path $resolvedRoot ".runtime"
    $bootstrapDirectory = Join-Path $runtimeRoot "bootstrap"
    $installLog = Join-Path $bootstrapDirectory "python-install.log"
    $requirementsStamp = Join-Path $bootstrapDirectory "requirements.sha256"
    $selectedPythonPath = Join-Path $bootstrapDirectory "selected-python.txt"
    $venvDirectory = Join-Path $runtimeRoot "venv"

    foreach ($requiredPath in @($startScript, $requirementsPath)) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "项目文件缺失，无法继续启动：$requiredPath（请重新解压完整的项目文件夹）"
        }
    }

    if (-not (Test-ProjectWritable -Root $resolvedRoot)) {
        throw "项目文件夹不可写：$resolvedRoot。安装环境需要在这里创建 .runtime 目录。请把整个项目移动到用户目录（例如桌面或文档）后再双击 start.bat，不要放在 C:\Program Files、只读压缩包内或受保护的同步目录。"
    }

    New-Item -ItemType Directory -Force -Path $bootstrapDirectory | Out-Null

    Initialize-InstallProgress
    Write-InstallBanner
    Start-InstallStep -Key "python"

    $forceEmbeddedPython = $env:MINDMAP_FORCE_EMBEDDED_PYTHON -eq "1"
    $forceLocalPython = ($env:MINDMAP_FORCE_LOCAL_PYTHON -eq "1") -or $forceEmbeddedPython
    $basePython = $null
    if ($forceEmbeddedPython) {
        $localPython = Join-Path $resolvedRoot ".runtime\python\python.exe"
        $embeddedMarker = Join-Path $resolvedRoot ".runtime\python\.mindmap-embedded"
        if ((Test-Path -LiteralPath $localPython -PathType Leaf) -and (Test-Path -LiteralPath $embeddedMarker -PathType Leaf)) {
            $basePython = Find-PythonInterpreter `
                -Candidates @(
                    [pscustomobject]@{
                        Executable = $localPython
                        PrefixArgs = @()
                        DirectEnvironment = $true
                    }
                ) `
                -MinimumVersion ([version]"3.10")
        }
    }
    else {
        $candidates = @(Get-PythonCandidates -Root $resolvedRoot -ForceLocalPython:$forceLocalPython)
        $basePython = Find-PythonInterpreter -Candidates $candidates -MinimumVersion ([version]"3.10")
    }

    if ($null -eq $basePython) {
        $basePython = Install-LocalPython `
            -Root $resolvedRoot `
            -LogPath $installLog `
            -ForceEmbedded:$forceEmbeddedPython
    }
    elseif (-not $basePython.DirectEnvironment -and -not (Test-PythonHasVenv -Python $basePython)) {
        # 系统 Python 缺 venv/ensurepip（精简安装很常见）。先试项目目录里的完整 Python，
        # 都没有就用嵌入式 Python，不要让用户卡在“创建虚拟环境失败”。
        Write-Host "检测到的 Python 缺少 venv 组件：$($basePython.Executable)"
        $replacement = Find-PythonInterpreter `
            -Candidates @([pscustomobject]@{ Executable = (Join-Path $resolvedRoot ".runtime\python\python.exe"); PrefixArgs = @() }) `
            -MinimumVersion ([version]"3.10")
        if ($null -eq $replacement) {
            $replacement = Install-LocalPython -Root $resolvedRoot -LogPath $installLog
        }
        if ($null -ne $replacement -and -not $replacement.DirectEnvironment -and -not (Test-PythonHasVenv -Python $replacement)) {
            # 换上的仍是缺 venv 的系统 Python，才退回嵌入式 Python
            #（嵌入式 Python 本来就不带 venv，直接使用即可）。
            $replacement = Install-EmbeddedPython -Root $resolvedRoot
        }
        $basePython = $replacement
    }
    else {
        Write-Host "检测到 Python $($basePython.Version)：$($basePython.Executable)"
    }

    Complete-InstallStep -Key "python"
    Start-InstallStep -Key "venv"

    $venvPython = $null
    if ($basePython.DirectEnvironment) {
        $venvPython = $basePython
        Write-Host "使用项目内嵌入式 Python 环境：$($venvPython.Executable)"
    }
    else {
        $venvPython = Ensure-ProjectVirtualEnvironment `
            -BasePython $basePython `
            -VenvDirectory $venvDirectory
        if ($null -eq $venvPython) {
            # 系统 Python 缺 venv / 目录受限时不再让用户自己去修，直接换嵌入式 Python。
            Write-Host "系统 Python 无法创建项目虚拟环境（可能缺少 venv 组件），正在改用项目内嵌入式 Python..."
            $basePython = Install-EmbeddedPython -Root $resolvedRoot
            $venvPython = $basePython
        }
        else {
            Write-Host "使用项目虚拟环境：$($venvPython.Executable)"
        }
    }

    Complete-InstallStep -Key "venv"
    Start-InstallStep -Key "deps"

    Invoke-DependencySetup `
        -PythonPath $venvPython.Executable `
        -RequirementsPath $requirementsPath `
        -StampPath $requirementsStamp

    Complete-InstallStep -Key "verify"

    Set-Content `
        -LiteralPath $selectedPythonPath `
        -Value "$($basePython.Executable)`n$($venvPython.Executable)" `
        -Encoding UTF8

    if ($PrepareOnly) {
        Write-InstallSummary
        Write-Host "环境准备完成。" -ForegroundColor Green
        return 0
    }

    Start-InstallStep -Key "launch"
    Complete-InstallStep -Key "launch"
    Write-InstallSummary

    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONNOUSERSITE = "1"
    Write-Host "正在启动 AnythingMindmap..."
    # 服务运行期间的日志（uvicorn 写 stderr）必须实时显示，所以不能缓冲，只放宽 stderr 判定。
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $venvPython.Executable $startScript 2>&1 | ForEach-Object { Write-Host $_ }
        $applicationExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($null -eq $applicationExitCode) {
        $applicationExitCode = 0
    }
    return $applicationExitCode
}

Export-ModuleMember -Function `
    Initialize-InstallProgress, `
    Write-InstallBanner, `
    Write-InstallProgress, `
    Get-InstallProgressLines, `
    Start-InstallStep, `
    Complete-InstallStep, `
    Fail-InstallStep, `
    Fail-ActiveInstallStep, `
    Write-InstallSummary, `
    Find-PythonInterpreter, `
    Test-PythonHasVenv, `
    Get-PythonInstallerSource, `
    Get-EmbeddedPythonSource, `
    Get-PipBootstrapSource, `
    Get-PipIndexSource, `
    Invoke-MirrorAction, `
    Get-PythonInstallerArguments, `
    Get-SilentPythonInstallerArguments, `
    Initialize-EmbeddedPythonRuntime, `
    Test-PythonInstallerNeedsFallback, `
    Test-DependenciesCurrent, `
    Test-ProjectWritable, `
    Ensure-ProjectVirtualEnvironment, `
    Invoke-DependencySetup, `
    Invoke-AnythingMindmapBootstrap
