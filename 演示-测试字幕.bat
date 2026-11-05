@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   演示模式：播放一段测试语音
echo   用来验证 麦克风 -^> 字幕 -^> OBS 整个流程
echo ============================================
echo.
python -X utf8 "%~dp0app.py" --demo
if errorlevel 1 (
    echo.
    echo 如果提示找不到 Python，请先运行「安装依赖.bat」。
)
pause
