param(
    [int]$IntervalHours = 0,
    [switch]$Disabled
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SchedulerPath = Join-Path $ProjectRoot "scheduler.py"
$SchedulerConfigPath = Join-Path $ProjectRoot "config\scheduler.json"
$TaskName = "CyberIntelligenceAgency"

if ($IntervalHours -le 0) {
    $IntervalHours = 3
    if (Test-Path -LiteralPath $SchedulerConfigPath) {
        $SchedulerConfig = Get-Content -Raw -Encoding UTF8 $SchedulerConfigPath | ConvertFrom-Json
        if ($SchedulerConfig.interval_hours -gt 0) {
            $IntervalHours = [int]$SchedulerConfig.interval_hours
        }
    }
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python virtual environment not found: $PythonPath"
}

$TaskCommand = '"' + $PythonPath + '" "' + $SchedulerPath + '"'
if ($IntervalHours -in @(1, 3, 6)) {
    & schtasks.exe /Create /TN $TaskName /SC HOURLY /MO $IntervalHours /TR $TaskCommand /F
}
elseif ($IntervalHours -eq 24) {
    & schtasks.exe /Create /TN $TaskName /SC DAILY /MO 1 /TR $TaskCommand /F
}
elseif ($IntervalHours -eq 168) {
    & schtasks.exe /Create /TN $TaskName /SC WEEKLY /MO 1 /TR $TaskCommand /F
}
else {
    throw "Unsupported interval: $IntervalHours"
}
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create Windows scheduled task. Exit code: $LASTEXITCODE"
}

$Settings = (Get-ScheduledTask -TaskName $TaskName).Settings
$Settings.DisallowStartIfOnBatteries = $false
$Settings.StopIfGoingOnBatteries = $false
$Settings.StartWhenAvailable = $true
$Settings.WakeToRun = $true
$Settings.ExecutionTimeLimit = "PT4H"
Set-ScheduledTask -TaskName $TaskName -Settings $Settings | Out-Null

if ($Disabled) {
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
}

$TaskState = if ($Disabled) { "disabled" } else { "enabled" }
Write-Output "Scheduled task created: $TaskName. Interval: $IntervalHours hour(s). State: $TaskState."
