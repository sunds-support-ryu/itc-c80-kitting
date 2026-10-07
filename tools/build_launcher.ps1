param([string]$Python = 'python')
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $pythonPrefix = & $Python -c 'import sys; print(sys.prefix)'
    $nativeArgs = @()
    foreach ($dll in @('ffi.dll','yaml.dll','tcl86t.dll','tk86t.dll')) {
        $dllPath = Join-Path $pythonPrefix "Library/bin/$dll"
        if (Test-Path -LiteralPath $dllPath) { $nativeArgs += @('--add-binary', "$dllPath;.") }
    }
    foreach ($library in @(@('tcl8.6','_tcl_data'),@('tk8.6','_tk_data'))) {
        $libraryPath = Join-Path $pythonPrefix "Library/lib/$($library[0])"
        if (Test-Path -LiteralPath $libraryPath) { $nativeArgs += @('--add-data', "$libraryPath;$($library[1])") }
    }
    & $Python -m PyInstaller --onefile --windowed --name 'ITC-C80-Launcher-1.0' --distpath 'release/native' --workpath 'data/build/native_launcher' --specpath 'data/build/native_launcher' --exclude-module numpy --exclude-module pandas --exclude-module scipy @nativeArgs 'src/launcher.py'
    if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed' }
    & $Python 'tools/build_release.py' --version '1.1.0' --launcher 'release/native/ITC-C80-Launcher-1.0.exe'
    if ($LASTEXITCODE -ne 0) { throw 'Release build failed' }
} finally {
    Pop-Location
}
