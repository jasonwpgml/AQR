param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = (Resolve-Path (Join-Path $ProjectRoot "..\.venv\Scripts\python.exe")).Path
$Prompts = Join-Path $ProjectRoot "src\ai_queue_runner\prompts"
$EntryPoint = Join-Path $ProjectRoot "src\ai_queue_runner\__main__.py"
$DistPath = Join-Path $ProjectRoot "dist"
$BuildPath = Join-Path $ProjectRoot "build\pyinstaller"
$SpecPath = Join-Path $ProjectRoot "build"

if (-not $SkipInstall) {
    & $Python -m pip install -e "$ProjectRoot[build]"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name AIQueueRunner `
    --distpath $DistPath `
    --workpath $BuildPath `
    --specpath $SpecPath `
    --add-data "$Prompts;ai_queue_runner/prompts" `
    $EntryPoint
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$Executable = Join-Path $DistPath "AIQueueRunner\AIQueueRunner.exe"
if (-not (Test-Path $Executable)) {
    throw "PyInstaller did not create $Executable"
}

& $Executable --version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "Built and smoke-tested: $Executable"
