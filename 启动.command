#!/bin/bash
# 抖加/垫付申请处理Agent 一键启动脚本（macOS/Linux）

echo "🚀 正在启动推广申请处理Agent..."

# 进入项目目录
cd "$(dirname "$0")"

# 检查Python
if ! command -v python3 &> /dev/null
then
    echo "❌ 未找到Python3，请先安装Python 3.9+"
    exit 1
fi

# 安装依赖（首次运行）
if [ ! -d ".venv" ]; then
    echo "📦 首次运行，正在安装依赖..."
    pip3 install -r requirements.txt
fi

# 杀掉旧进程
lsof -ti:8889 | xargs kill -9 2>/dev/null
sleep 1

# 启动服务
echo "✅ 服务启动中，浏览器自动打开..."
python3 app.py > /tmp/promotion_agent.log 2>&1 &

# 等待启动完成
sleep 3

# 打开浏览器
open http://localhost:8889

echo "
🎉 启动完成！
后台地址：http://localhost:8889
日志位置：/tmp/promotion_agent.log
按 Ctrl+C 停止服务
"
