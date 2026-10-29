Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$modulePath = Join-Path $root "scripts\windows_bootstrap\Bootstrap.psm1"

if (-not (Test-Path -LiteralPath $modulePath -PathType Leaf)) {
    throw "Bootstrap module not found: $modulePath"
}

Import-Module $modulePath -Force

$script:testsRun = 0

function Assert-Equal {
    param(
        [AllowNull()][object]$Actual,
        [AllowNull()][object]$Expected,
        [string]$Message
    )

    if ($Actual -ne $Expected) {
        throw "$Message. Expected [$Expected], got [$Actual]."
    }
}

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function New-FakePython {
    param(
        [string]$Directory,
        [string]$Name,
        [string]$Version
    )

    $isWindowsPlatform = $env:OS -eq "Windows_NT"
    if ($isWindowsPlatform) {
        $path = Join-Path $Directory "$Name.cmd"
        $script = @"
@echo off
if "%~1"=="-c" (
    echo $Version
    exit /b 0
)
exit /b 1
"@
        [System.IO.File]::WriteAllText($path, $script.Replace("`r`n", "`n").Replace("`n", "`r`n"))
        return $path
    }

    $path = Join-Path $Directory $Name
    $script = @"
#!/bin/sh
if [ "`$1" = "-c" ]; then
    printf '%s\n' '$Version'
    exit 0
fi
exit 1
"@
    [System.IO.File]::WriteAllText($path, $script.Replace("`r`n", "`n"))
    & chmod +x $path
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to make fake interpreter executable: $path"
    }
    return $path
}

function Invoke-Test {
    param(
        [string]$Name,
        [scriptblock]$Body
    )

    & $Body
    $script:testsRun += 1
    Write-Host "PASS $Name"
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("mindmap-bootstrap-test-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tempRoot | Out-Null

try {
    Invoke-Test "selects the first supported Python interpreter" {
        $oldPython = New-FakePython -Directory $tempRoot -Name "python-old" -Version "3.9.18"
        $supportedPython = New-FakePython -Directory $tempRoot -Name "python-supported" -Version "3.12.10"

        $selected = Find-PythonInterpreter -Candidates @(
            [pscustomobject]@{ Executable = $oldPython; PrefixArgs = @() },
            [pscustomobject]@{ Executable = $supportedPython; PrefixArgs = @() }
        ) -MinimumVersion ([version]"3.10")

        Assert-Equal -Actual $selected.Executable -Expected $supportedPython -Message "Wrong interpreter selected"
        Assert-Equal -Actual $selected.Version -Expected ([version]"3.12.10") -Message "Wrong version reported"
    }

    Invoke-Test "returns null when no Python interpreter is supported" {
        $oldPython = New-FakePython -Directory $tempRoot -Name "python-only-old" -Version "3.9.18"

        $selected = Find-PythonInterpreter -Candidates @(
            [pscustomobject]@{ Executable = $oldPython; PrefixArgs = @() }
        ) -MinimumVersion ([version]"3.10")

        Assert-Equal -Actual $selected -Expected $null -Message "Unsupported interpreter was selected"
    }

    Invoke-Test "finds per-user Python that is missing from PATH" {
        $oldLocalAppData = $env:LOCALAPPDATA
        try {
            $env:LOCALAPPDATA = Join-Path $tempRoot "localappdata"
            $pythonDirectory = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312"
            New-Item -ItemType Directory -Force -Path $pythonDirectory | Out-Null
            $pythonPath = Join-Path $pythonDirectory "python.exe"
            [System.IO.File]::WriteAllBytes($pythonPath, [byte[]]@(0))

            $module = Get-Module Bootstrap
            $candidates = @(& $module {
                param($projectRoot)
                Get-PythonCandidates -Root $projectRoot
            } $tempRoot)
            $candidatePaths = @($candidates | ForEach-Object { $_.Executable })

            Assert-True -Condition ($candidatePaths -contains $pythonPath) -Message "Per-user Python installation was not discovered"
        }
        finally {
            $env:LOCALAPPDATA = $oldLocalAppData
        }
    }

    Invoke-Test "preserves embedded-environment marker when selecting Python" {
        $embeddedPython = New-FakePython -Directory $tempRoot -Name "embedded-python" -Version "3.12.10"

        $selected = Find-PythonInterpreter -Candidates @(
            [pscustomobject]@{
                Executable = $embeddedPython
                PrefixArgs = @()
                DirectEnvironment = $true
            }
        ) -MinimumVersion ([version]"3.10")

        Assert-True -Condition $selected.DirectEnvironment -Message "Embedded environment marker was lost"
    }

    Invoke-Test "recognizes project-local embedded Python on later starts" {
        $projectRoot = Join-Path $tempRoot "embedded-project"
        $pythonDirectory = Join-Path $projectRoot ".runtime\python"
        New-Item -ItemType Directory -Force -Path $pythonDirectory | Out-Null
        [System.IO.File]::WriteAllBytes((Join-Path $pythonDirectory "python.exe"), [byte[]]@(0))
        [System.IO.File]::WriteAllText((Join-Path $pythonDirectory ".mindmap-embedded"), "")

        $module = Get-Module Bootstrap
        $candidates = @(& $module {
            param($rootPath)
            Get-PythonCandidates -Root $rootPath
        } $projectRoot)
        $embeddedCandidate = $candidates |
            Where-Object { $_.Executable -eq (Join-Path $pythonDirectory "python.exe") } |
            Select-Object -First 1

        Assert-True -Condition ($null -ne $embeddedCandidate) -Message "Project-local embedded Python was not discovered"
        Assert-True -Condition $embeddedCandidate.DirectEnvironment -Message "Project-local embedded Python was not marked as a direct environment"
    }

    Invoke-Test "enables site packages in embedded Python runtime" {
        $pythonDirectory = Join-Path $tempRoot "embedded-runtime"
        New-Item -ItemType Directory -Force -Path $pythonDirectory | Out-Null
        $pthPath = Join-Path $pythonDirectory "python312._pth"
        [System.IO.File]::WriteAllText($pthPath, "python312.zip`r`n.`r`n`r`n#import site`r`n")
        [System.IO.File]::WriteAllBytes((Join-Path $pythonDirectory "python312.zip"), [byte[]]@())

        Initialize-EmbeddedPythonRuntime -PythonDirectory $pythonDirectory

        $sitePackages = Join-Path $pythonDirectory "Lib\site-packages"
        $pthText = Get-Content -LiteralPath $pthPath -Raw
        Assert-True -Condition (Test-Path -LiteralPath $sitePackages -PathType Container) -Message "site-packages directory was not created"
        Assert-True -Condition ($pthText -match "(?m)^Lib\\site-packages\r?$") -Message "site-packages was not added to the embedded Python path"
        Assert-True -Condition ($pthText -match "(?m)^import site\r?$") -Message "site import was not enabled"
    }

    Invoke-Test "installs Python per-user into the project without changing PATH" {
        $arguments = Get-PythonInstallerArguments `
            -TargetDirectory "D:\Project Folder\.runtime\python" `
            -LogPath "D:\Project Folder\.runtime\bootstrap\python-install.log"

        Assert-True -Condition ($arguments -contains "InstallAllUsers=0") -Message "Installer may require administrator rights"
        Assert-True -Condition ($arguments -contains "PrependPath=0") -Message "Installer may modify the system PATH"
        Assert-True -Condition ($arguments -contains "Include_pip=1") -Message "Installer does not include pip"
        Assert-True -Condition ($arguments -contains 'TargetDir="D:\Project Folder\.runtime\python"') -Message "Installer target is not quoted or project-local"
        Assert-True -Condition ($arguments -contains '"D:\Project Folder\.runtime\bootstrap\python-install.log"') -Message "Installer log path is not quoted"
        Assert-True -Condition (-not ($arguments -contains "InstallAllUsers=1")) -Message "All-users install was requested"
        Assert-True -Condition (-not ($arguments -contains "PrependPath=1")) -Message "System PATH modification was requested"
    }

    Invoke-Test "requires dependency installation when requirements changed" {
        $requirementsPath = Join-Path $tempRoot "requirements-changed.txt"
        $stampPath = Join-Path $tempRoot "requirements-changed.sha256"
        [System.IO.File]::WriteAllText($requirementsPath, "fastapi`nuvicorn`n")
        [System.IO.File]::WriteAllText($stampPath, "8c2b3b636159723d4cd159014afba238ac4fbc8046df5dc2a863421d236b93d6")

        $current = Test-DependenciesCurrent `
            -RequirementsPath $requirementsPath `
            -StampPath $stampPath `
            -DependencyProbe { return $true }

        Assert-True -Condition (-not $current) -Message "Changed requirements were treated as current"
    }

    Invoke-Test "requires dependency installation when imports are missing" {
        $requirementsPath = Join-Path $tempRoot "requirements-missing-import.txt"
        $stampPath = Join-Path $tempRoot "requirements-missing-import.sha256"
        [System.IO.File]::WriteAllText($requirementsPath, "fastapi`nuvicorn`n")
        [System.IO.File]::WriteAllText(
            $stampPath,
            "e9af2913de609d1de45b2fbab270c5a77281543c4dab6d1dd182f221fd04f683"
        )

        $current = Test-DependenciesCurrent `
            -RequirementsPath $requirementsPath `
            -StampPath $stampPath `
            -DependencyProbe { return $false }

        Assert-True -Condition (-not $current) -Message "Missing imports were treated as installed"
    }

    Invoke-Test "skips dependency installation only when hash and imports match" {
        $requirementsPath = Join-Path $tempRoot "requirements-current.txt"
        $stampPath = Join-Path $tempRoot "requirements-current.sha256"
        [System.IO.File]::WriteAllText($requirementsPath, "fastapi`nuvicorn`n")
        [System.IO.File]::WriteAllText(
            $stampPath,
            "e9af2913de609d1de45b2fbab270c5a77281543c4dab6d1dd182f221fd04f683"
        )

        $current = Test-DependenciesCurrent `
            -RequirementsPath $requirementsPath `
            -StampPath $stampPath `
            -DependencyProbe { return $true }

        Assert-True -Condition $current -Message "Current dependencies triggered a reinstall"
    }

    Invoke-Test "prefers domestic Python mirrors and keeps the official source last" {
        $sources = @(Get-PythonInstallerSource)

        Assert-True -Condition ($sources.Count -ge 3) -Message "Not enough Python download sources"
        Assert-True -Condition ($sources[0].Url -like "https://mirrors.huaweicloud.com/*") -Message "Huawei mirror is not first"
        Assert-True -Condition ($sources[1].Url -like "https://mirrors.tuna.tsinghua.edu.cn/*") -Message "Tsinghua mirror is not second"
        Assert-True -Condition ($sources[-1].Url -like "https://www.python.org/*") -Message "Official Python source is not the final fallback"
    }

    Invoke-Test "prefers domestic pip mirrors and keeps official PyPI last" {
        $sources = @(Get-PipIndexSource)

        Assert-True -Condition ($sources.Count -ge 3) -Message "Not enough pip download sources"
        Assert-True -Condition ($sources[0].Url -eq "https://pypi.tuna.tsinghua.edu.cn/simple") -Message "Tsinghua PyPI is not first"
        Assert-True -Condition ($sources[1].Url -eq "https://mirrors.aliyun.com/pypi/simple/") -Message "Aliyun PyPI is not second"
        Assert-True -Condition ($sources[-1].Url -eq "https://pypi.org/simple") -Message "Official PyPI is not the final fallback"
    }

    Invoke-Test "prefers domestic embedded Python mirrors and keeps official source last" {
        $sources = @(Get-EmbeddedPythonSource)

        Assert-True -Condition ($sources.Count -ge 3) -Message "Not enough embedded Python sources"
        Assert-True -Condition ($sources[0].Url -like "https://mirrors.huaweicloud.com/*embed-amd64.zip") -Message "Huawei embedded Python mirror is not first"
        Assert-True -Condition ($sources[-1].Url -like "https://www.python.org/*embed-amd64.zip") -Message "Official embedded Python source is not the final fallback"
    }

    Invoke-Test "prefers domestic pip bootstrap mirrors and keeps official source last" {
        $sources = @(Get-PipBootstrapSource)

        Assert-True -Condition ($sources.Count -ge 3) -Message "Not enough pip bootstrap sources"
        Assert-True -Condition ($sources[0].Url -like "https://pypi.tuna.tsinghua.edu.cn/*pip-25.2-py3-none-any.whl") -Message "Tsinghua pip bootstrap mirror is not first"
        Assert-True -Condition ($sources[-1].Url -like "https://files.pythonhosted.org/*pip-25.2-py3-none-any.whl") -Message "Official pip bootstrap source is not the final fallback"
    }

    Invoke-Test "falls back to the next mirror when the current source fails" {
        $sources = @(
            [pscustomobject]@{ Name = "镜像A"; Url = "https://mirror-a.invalid" },
            [pscustomobject]@{ Name = "镜像B"; Url = "https://mirror-b.invalid" }
        )
        $attempts = [System.Collections.Generic.List[string]]::new()
        $action = {
            param($source)
            $attempts.Add($source.Name)
            if ($source.Name -eq "镜像A") {
                throw "连接超时"
            }
            return $source.Url
        }.GetNewClosure()

        $result = Invoke-MirrorAction `
            -Source $sources `
            -OperationName "下载测试文件" `
            -Action $action

        Assert-Equal -Actual $result -Expected "https://mirror-b.invalid" -Message "Fallback source was not used"
        Assert-Equal -Actual ($attempts -join ",") -Expected "镜像A,镜像A,镜像B" -Message "Mirrors were not retried then failed over in order"
    }

    Invoke-Test "recovers when a mirror succeeds on retry" {
        $sources = @(
            [pscustomobject]@{ Name = "镜像A"; Url = "https://mirror-a.invalid" }
        )
        $attempts = [System.Collections.Generic.List[int]]::new()
        $action = {
            param($source)
            $attempts.Add(1)
            if ($attempts.Count -eq 1) {
                throw "临时超时"
            }
            return $source.Url
        }.GetNewClosure()

        $result = Invoke-MirrorAction `
            -Source $sources `
            -OperationName "下载测试文件" `
            -Action $action

        Assert-Equal -Actual $result -Expected "https://mirror-a.invalid" -Message "Retry did not recover the mirror"
        Assert-Equal -Actual $attempts.Count -Expected 2 -Message "Unexpected number of attempts"
    }

    Invoke-Test "reports all mirror failures with Chinese guidance" {
        $sources = @(
            [pscustomobject]@{ Name = "镜像A"; Url = "https://mirror-a.invalid" },
            [pscustomobject]@{ Name = "镜像B"; Url = "https://mirror-b.invalid" }
        )
        $failureMessage = $null

        try {
            Invoke-MirrorAction `
                -Source $sources `
                -OperationName "下载测试文件" `
                -Action { param($source) throw "连接超时" } | Out-Null
        }
        catch {
            $failureMessage = $_.Exception.Message
        }

        Assert-True -Condition ($failureMessage -match "所有镜像源均失败") -Message "Final failure does not explain the mirror failures in Chinese"
        Assert-True -Condition ($failureMessage -match "请检查网络或代理") -Message "Final failure does not include Chinese recovery guidance"
    }

    Invoke-Test "reports startup failures with Chinese cause and guidance" {
        $emptyRoot = Join-Path $tempRoot "empty-project"
        New-Item -ItemType Directory -Path $emptyRoot | Out-Null
        $pwshPath = (Get-Process -Id $PID).Path

        $output = & $pwshPath `
            -NoLogo `
            -NoProfile `
            -File (Join-Path $root "scripts\bootstrap_windows.ps1") `
            -Root $emptyRoot 2>&1
        $exitCode = $LASTEXITCODE
        $text = $output -join "`n"

        Assert-Equal -Actual $exitCode -Expected 1 -Message "Startup failure returned the wrong exit code"
        Assert-True -Condition ($text -match "环境准备失败") -Message "Startup failure does not have a Chinese headline"
        Assert-True -Condition ($text -match "具体原因") -Message "Startup failure does not explain the cause in Chinese"
        Assert-True -Condition ($text -match "处理建议") -Message "Startup failure does not provide Chinese recovery guidance"
    }

    Invoke-Test "writes startup failure details to .runtime bootstrap log" {
        $emptyRoot = Join-Path $tempRoot "empty-project-with-log"
        New-Item -ItemType Directory -Path $emptyRoot | Out-Null
        $pwshPath = (Get-Process -Id $PID).Path

        & $pwshPath `
            -NoLogo `
            -NoProfile `
            -File (Join-Path $root "scripts\bootstrap_windows.ps1") `
            -Root $emptyRoot 2>&1 | Out-Null

        $logPath = Join-Path $emptyRoot ".runtime\bootstrap\bootstrap.log"
        Assert-True -Condition (Test-Path -LiteralPath $logPath -PathType Leaf) -Message "Startup failure log was not created"
        $logText = Get-Content -LiteralPath $logPath -Raw
        Assert-True -Condition ($logText -match "环境准备失败") -Message "Startup failure log does not contain a Chinese cause"
        Assert-True -Condition ($logText -match "项目文件缺失") -Message "Startup failure log does not contain the specific failure"
    }

    Invoke-Test "skips a modify-only Windows installer and uses embedded fallback" {
        $logPath = Join-Path $tempRoot "python-install-modify.log"
        $runtimePython = Join-Path $tempRoot "missing-python\python.exe"
        [System.IO.File]::WriteAllText(
            $logPath,
            "i410: Variable: ActionLikeInstallation = Modify`r`ni410: Variable: WixBundleInstalled = 1`r`n"
        )

        $needsFallback = Test-PythonInstallerNeedsFallback `
            -LogPath $logPath `
            -RuntimePython $runtimePython

        Assert-True -Condition $needsFallback -Message "Modify-only installer was not skipped"
    }

    Invoke-Test "detects whether a Python provides venv and ensurepip" {
        $module = Get-Module Bootstrap
        $withVenv = $null
        $withoutVenv = $null

        if ($env:OS -eq "Windows_NT") {
            $withVenv = Join-Path $tempRoot "python-venv-ok.cmd"
            [System.IO.File]::WriteAllText(
                $withVenv,
                "@echo off`r`nif `"%~1`"==`"-c`" (exit /b 0)`r`nexit /b 1`r`n"
            )
            $withoutVenv = Join-Path $tempRoot "python-venv-missing.cmd"
            [System.IO.File]::WriteAllText(
                $withoutVenv,
                "@echo off`r`nexit /b 1`r`n"
            )
        }
        else {
            $withVenv = Join-Path $tempRoot "python-venv-ok"
            [System.IO.File]::WriteAllText($withVenv, "#!/bin/sh`nexit 0`n")
            $withoutVenv = Join-Path $tempRoot "python-venv-missing"
            [System.IO.File]::WriteAllText($withoutVenv, "#!/bin/sh`nexit 1`n")
            & chmod +x $withVenv $withoutVenv
        }

        $hasVenv = & $module {
            param($path)
            Test-PythonHasVenv -Python ([pscustomobject]@{ Executable = $path; PrefixArgs = @() })
        } $withVenv
        Assert-True -Condition $hasVenv -Message "Python with venv was reported as missing it"

        $missingVenv = & $module {
            param($path)
            Test-PythonHasVenv -Python ([pscustomobject]@{ Executable = $path; PrefixArgs = @() })
        } $withoutVenv
        Assert-True -Condition (-not $missingVenv) -Message "Python without venv was reported as usable"
    }

    Invoke-Test "reports a project directory that cannot be written to" {
        $writable = Test-ProjectWritable -Root $tempRoot
        Assert-True -Condition $writable -Message "Writable temp directory was reported as read-only"
    }

    Invoke-Test "uses a more conservative installer argument set as retry" {
        $normal = Get-PythonInstallerArguments -TargetDirectory "C:\temp\python" -LogPath "C:\temp\install.log"
        $silent = Get-SilentPythonInstallerArguments -TargetDirectory "C:\temp\python" -LogPath "C:\temp\install.log"

        Assert-True -Condition ($silent -contains "SimpleInstall=1") -Message "Retry arguments lost SimpleInstall"
        Assert-True -Condition ($silent -contains "InstallAllUsers=0") -Message "Retry arguments would require elevation"
        Assert-True -Condition (-not ($normal -contains "SimpleInstall=1")) -Message "Normal arguments changed unexpectedly"
    }

    Write-Host "PASS all $script:testsRun Windows bootstrap tests"
}
finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
