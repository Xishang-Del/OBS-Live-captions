@echo off
setlocal EnableExtensions
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title 实时字幕 - 一键安装依赖

echo ============================================
echo    实时字幕 一键安装（Python + 依赖 + 模型）
echo ============================================
echo.

set "PY="
call :find_python
if not defined PY goto :install_python

:got_python
echo [1/4] 使用 Python: %PY%
%PY% --version

echo.
echo [2/4] 安装 Python 依赖（使用清华镜像加速）...
%PY% -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>nul
%PY% -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo.
    echo 清华镜像安装失败，改用官方源重试...
    %PY% -m pip install -r requirements.txt
)
if errorlevel 1 (
    echo.
    echo [失败] 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
)

echo.
echo [3/4] 可选：NVIDIA 显卡加速（GTX/RTX 用户建议装，约 1.5GB）
choice /c yn /n /t 20 /d y /m "是否安装 CUDA 加速库？[Y/N] "
if errorlevel 2 goto :skip_cuda
%PY% -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cuda-runtime-cu12 -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 echo [提示] CUDA 库安装失败，程序会自动使用 CPU，不影响使用。
:skip_cuda

echo.
echo [4/4] 检查依赖...
%PY% -c "import faster_whisper, sounddevice, numpy, scipy, webrtcvad, customtkinter, pyrnnoise; print('依赖检查通过')"
if errorlevel 1 (
    echo [失败] 依赖检查没通过，请把上面的报错发给开发者。
    pause
    exit /b 1
)

echo.
choice /c yn /n /t 15 /d y /m "是否现在下载中文模型 SenseVoice（约 230MB，推荐）？[Y/N] "
if errorlevel 2 goto :skip_model
%PY% -X utf8 model_download.py sensevoice
:skip_model

echo.
echo ============================================
echo   安装完成！双击「启动实时字幕.bat」开始使用。
echo ============================================
pause
exit /b 0

:find_python
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "PY=py -3"
    exit /b 0
)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
if not errorlevel 1 (
    set "PY=python"
    exit /b 0
)
exit /b 0

:install_python
echo 没有检测到 Python 3.10 以上版本，尝试自动安装...
where winget >nul 2>nul
if errorlevel 1 goto :manual_python

echo 正在通过 winget 安装 Python 3.12（需要联网，可能会弹权限窗口）...
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%LOCALAPPDATA%\Programs\Python\Launcher;%PATH%"
call :find_python
if not defined PY goto :manual_python
goto :got_python

:manual_python
echo.
echo ============================================
echo  需要手动安装 Python
echo ============================================
echo 1. 即将打开 Python 官网下载页
echo 2. 安装时务必勾选 "Add python.exe to PATH"
echo 3. 装完后重新双击本脚本
start "" https://www.python.org/downloads/
pause
exit /b 1
