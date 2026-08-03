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
    echo [警告] 未设置环境变量 TS_TOKEN：个股搜索和盘后更新将无法加载行情
    echo         设置方法：setx TS_TOKEN 你的token  ，然后重开本窗口
    echo.
)

echo ============================================
echo   RYAN K线推背图 ^| 高速缓存模式
echo   正在启动后端服务（127.0.0.1:8600）...
echo   K线、指标、推背图使用统一接口与两级缓存
echo ============================================

start "" /min cmd /c "timeout /t 4 /nobreak >nul && start http://127.0.0.1:8600"
".venv\Scripts\python.exe" -m backend.app.main_fast

pause
