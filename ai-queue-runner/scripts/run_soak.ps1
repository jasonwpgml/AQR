param(
    [double]$Hours = 8
)

$ErrorActionPreference = "Stop"
if ($Hours -le 0) {
    throw "Hours must be greater than zero"
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = (Resolve-Path (Join-Path $ProjectRoot "..\.venv\Scripts\python.exe")).Path
$Deadline = [DateTimeOffset]::UtcNow.AddHours($Hours)
$Runs = 0

Push-Location $ProjectRoot
try {
    do {
        & $Python -m pytest -q tests\integration\test_soak.py
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        $Runs += 1
        Write-Output "soak_iteration=$Runs utc=$([DateTimeOffset]::UtcNow.ToString('O'))"
    } while ([DateTimeOffset]::UtcNow -lt $Deadline)
}
finally {
    Pop-Location
}

Write-Output "Completed $Runs soak iterations over $Hours hour(s)."
