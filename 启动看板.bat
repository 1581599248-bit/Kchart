@echo off
chcp 65001 >nul
title RYAN K线推背图 - 启动中
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到 .venv，请先运行 scripts\setup_env.bat 初始化环境
    pause
    exit /b 1
)

echo ============================================
echo   RYAN K线推背图 ^| 技术面多因子打分系统
echo   正在启动后端服务（127.0.0.1:8600）...
echo   首次启动需预计算打分缓存，请稍候
echo ============================================

start "" /min cmd /c "timeout /t 4 /nobreak >nul && start http://127.0.0.1:8600"
".venv\Scripts\python.exe" -m backend.app.main

pause
