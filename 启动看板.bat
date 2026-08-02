@echo off
chcp 65001 >nul
title RYAN K线推背图 - 启动中
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到 .venv，请先运行 scripts\setup_env.bat 初始化环境
    pause
    exit /b 1
)

if "%TS_TOKEN%"=="" (
    echo [警告] 未设置环境变量 TS_TOKEN：行情数据将无法加载（TOP20 榜单仍可查看）
    echo         设置方法：setx TS_TOKEN 你的token  ，然后重开本窗口
    echo.
)

echo ============================================
echo   RYAN K线推背图 ^| 技术面多因子打分系统
echo   正在启动后端服务（127.0.0.1:8600）...
echo   数据来自 API 实时拉取，首次打开某标的会稍慢
echo ============================================

start "" /min cmd /c "timeout /t 4 /nobreak >nul && start http://127.0.0.1:8600"
".venv\Scripts\python.exe" -m backend.app.main

pause
