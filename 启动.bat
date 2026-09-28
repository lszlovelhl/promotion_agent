@echo off
chcp 65001
echo 🚀 正在启动推广申请处理Agent...

cd /d "%~dp0"

:: 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未找到Python，请先安装Python 3.9+
    pause
    exit /b 1
)

:: 安装依赖（首次运行）
if not exist ".venv" (
    echo 📦 首次运行，正在安装依赖...
    pip install -r requirements.txt
)

:: 杀掉旧进程
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8889 ^| findstr LISTENING') do taskkill /f /pid %%a >nul 2>&1
timeout /t 1 /nobreak >nul

:: 启动服务
echo ✅ 服务启动中，浏览器自动打开...
start "" http://localhost:8889
python app.py

pause
