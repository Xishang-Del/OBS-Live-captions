@echo off
chcp 65001 >nul
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 goto :missing

start "" pythonw -X utf8 "%~dp0app.py"
exit /b 0

:missing
echo.
echo [错误] 没有找到 Python。
echo 请先双击「安装依赖.bat」，它会自动安装 Python 和所有依赖。
echo.
pause
exit /b 1
