param(
    [switch]$Development,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

function Invoke-CheckedPython {
    param([string]$Executable, [string[]]$PythonArgs)
    & $Executable @PythonArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed (exit $LASTEXITCODE). Installation stopped."
    }
}

if ($env:OS -ne "Windows_NT") { throw "This release supports Windows only." }

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $BootstrapArgs = @()
    if ($PythonPath) {
        $BootstrapPython = $PythonPath
    }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $BootstrapPython = "py"
        $BootstrapArgs = @("-3.11")
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $BootstrapPython = "python"
    }
    else {
        throw "Install Python 3.11 x64 from https://www.python.org/downloads/ first."
    }
    $VersionCheck = "import sys,struct; sys.exit(0 if sys.version_info[:2] == (3,11) and struct.calcsize('P') == 8 else 1)"
    Invoke-CheckedPython $BootstrapPython ($BootstrapArgs + @("-c", $VersionCheck))
    Invoke-CheckedPython $BootstrapPython ($BootstrapArgs + @("-m", "venv", (Join-Path $ProjectRoot ".venv")))
}

Invoke-CheckedPython $VenvPython @("-c", "import sys,struct; sys.exit(0 if sys.version_info[:2] == (3,11) and struct.calcsize('P') == 8 else 1)")
$LockName = if ($Development) { "requirements-dev-windows-py311.lock" } else { "requirements-windows-py311.lock" }
Invoke-CheckedPython $VenvPython @("-m", "pip", "install", "-r", (Join-Path $ProjectRoot $LockName))
Invoke-CheckedPython $VenvPython @("-m", "pip", "check")

foreach ($Directory in @("config", "data", "output", "knowledge\images", "models")) {
    New-Item -ItemType Directory -Path (Join-Path $ProjectRoot $Directory) -Force | Out-Null
}
Invoke-CheckedPython $VenvPython @((Join-Path $ProjectRoot "scripts\doctor.py"), "--strict")
Write-Output "Installation complete. Existing settings and login sessions were preserved."
Write-Output "Start: $VenvPython desktop.py"
