@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo 创建虚拟环境...
"%USERPROFILE%\.local\bin\python3" -m venv .venv
echo 安装依赖...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
echo 完成。双击 启动看板.bat 打开系统。
pause
