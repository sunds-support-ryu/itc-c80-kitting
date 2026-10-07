@echo off
chcp 65001 >nul
cd /d "%~dp0"

title Camera Inspection Tool

echo ================================
echo Camera Inspection Tool
echo ================================
echo.

where py >nul 2>nul

if %errorlevel%==0 (
    py "src\bootstrap.py"
    goto END
)

where python >nul 2>nul

if %errorlevel%==0 (
    python "src\bootstrap.py"
    goto END
)

echo [NG] Python が見つかりません。
echo Pythonをインストールしてください。
echo.

pause
exit /b 1


:END
echo.
echo ================================
echo 終了
echo ================================
pause