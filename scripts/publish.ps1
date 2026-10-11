param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryUrl
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

function Invoke-CheckedGit {
    param([string[]]$GitArgs)
    & git -C $ProjectRoot @GitArgs
    if ($LASTEXITCODE -ne 0) { throw "Git command failed (exit $LASTEXITCODE). No force push will be attempted." }
}

if ($RepositoryUrl -notmatch '^https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+(?:\.git)?$') {
    throw "Provide the HTTPS URL of your intended GitHub repository; no credentials in the URL."
}
if (-not (Test-Path -LiteralPath $PythonPath)) { throw "Run scripts/setup.ps1 first." }
$Branch = & git -C $ProjectRoot branch --show-current
if ($LASTEXITCODE -ne 0 -or $Branch -ne "main") { throw "Check out the prepared main branch first." }
$Dirty = & git -C $ProjectRoot status --porcelain
if ($LASTEXITCODE -ne 0 -or $Dirty) { throw "Commit all source changes before publishing." }
$Version = (Get-Content -LiteralPath (Join-Path $ProjectRoot "VERSION") -Raw).Trim()
if ($Version -notmatch '^\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?$') { throw "Invalid VERSION." }
$ReleaseTag = "v$Version"
$HeadCommit = & git -C $ProjectRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw "A release commit is required." }
$TagCommit = & git -C $ProjectRoot rev-parse --verify "$ReleaseTag^{commit}"
if ($LASTEXITCODE -ne 0 -or $HeadCommit -ne $TagCommit) { throw "The release tag must refer to the current commit." }
& $PythonPath (Join-Path $ProjectRoot "scripts\release.py") check --ref HEAD
if ($LASTEXITCODE -ne 0) { throw "Release content scan failed." }

$Remotes = @(& git -C $ProjectRoot remote)
if ($LASTEXITCODE -ne 0) { throw "Cannot read Git remotes." }
if ($Remotes -contains "origin") {
    $CurrentRemote = & git -C $ProjectRoot remote get-url origin
    if ($LASTEXITCODE -ne 0) { throw "Cannot read origin URL." }
    if ($CurrentRemote.TrimEnd('/') -ne $RepositoryUrl.TrimEnd('/')) {
        throw "origin points to a different repository. Review it before publishing."
    }
}
else {
    Invoke-CheckedGit @("remote", "add", "origin", $RepositoryUrl)
}

Invoke-CheckedGit @("push", "--atomic", "-u", "origin", "main", $ReleaseTag)
Write-Output "Source and tag pushed. Check GitHub Actions, then publish the generated draft Release."
