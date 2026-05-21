@echo off
chcp 65001 >nul
title Build Interview Helper

echo ========================================
echo  Building Interview Helper
echo ========================================
echo.

:: Install dependencies
echo [1/3] Installing Python dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] pip install failed
    pause
    exit /b 1
)

:: Clean old builds
echo [2/3] Cleaning old builds...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist "InterviewHelper.spec" del "InterviewHelper.spec"
if exist "NotePad.spec" del "NotePad.spec"

:: Build with PyInstaller
echo [3/3] Building executable (this may take a few minutes)...
pyinstaller --onefile --windowed --name "InterviewHelper" main.py
if %errorlevel% neq 0 (
    echo [ERROR] PyInstaller build failed
    pause
    exit /b 1
)

:: Copy config alongside exe
copy config.json dist\config.json

echo.
echo ========================================
echo  Build complete!
echo  Output: dist\InterviewHelper.exe
echo  Config: dist\config.json
echo.
echo  Hotkeys:
echo    Alt+Q            - 切换输入框
echo    Ctrl+Shift+H     - 显示/隐藏窗口
echo    Alt+Shift+C      - 清除对话
echo    Ctrl+Shift+Q     - 退出程序
echo ========================================
pause
