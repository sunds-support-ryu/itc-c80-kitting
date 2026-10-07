param([string]$Python = 'python')
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    & $Python -m PyInstaller --onefile --console --name 'ITC-C80-Launcher-1.0' --distpath 'release/1.0' --workpath 'data/build/launcher' --specpath 'data/build/launcher' --exclude-module numpy --exclude-module pandas --exclude-module scipy 'src/launcher.py'
    if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed' }
    & $Python 'tools/build_release.py' --version '1.0'
    if ($LASTEXITCODE -ne 0) { throw 'Release build failed' }
} finally {
    Pop-Location
}
