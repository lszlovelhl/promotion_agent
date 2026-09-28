"""
推广申请处理 Agent - 后端服务
Master-Sub Agent 架构演示
"""
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional, Dict
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "agents"))

from agents.master import MasterAgent
from lark_client import LarkClient
from llm_client import LLMClient
import json

app = FastAPI(title="推广申请处理Agent")

# 初始化
lark_client = LarkClient()
llm_client = LLMClient()
master = MasterAgent(lark_client, llm_client)

# 配置文件
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")

# 飞书机器人
lark_bot = None


def auto_start_bot():
    """启动时自动启动机器人（如果已配置）"""
    global lark_bot
    config = load_config()
    app_id = config.get("app_id", "")
    app_secret = config.get("app_secret", "")
    
    if app_id and app_secret:
        try:
            from lark_bot import LarkBot
            lark_bot = LarkBot(app_id, app_secret, master)
            lark_bot.start()
            print("✅ 飞书机器人已自动启动")
        except Exception as e:
            print(f"⚠️ 飞书机器人启动失败: {e}")
    else:
        print("ℹ️ 未配置飞书机器人，跳过自动启动")


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_config(config: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


@app.get("/api/bot/status")
def get_bot_status():
    """获取机器人运行状态"""
    global lark_bot
    if lark_bot:
        return {
            "running": lark_bot._running,
            "auto_restart": True,
            "pending_count": len(lark_bot.pending_records) if lark_bot else 0
        }
    return {"running": False, "auto_restart": False}


@app.post("/api/bot/restart")
def restart_bot():
    """手动重启机器人"""
    global lark_bot
    if lark_bot:
        lark_bot.restart()
        return {"success": True, "message": "机器人已重启"}
    return {"success": False, "message": "机器人未初始化"}


# 请求模型
class ProcessTextRequest(BaseModel):
    text: str
    app_type: str = "auto"


class ConfirmRecordsRequest(BaseModel):
    records: List[dict]


class LarkConfigRequest(BaseModel):
    app_id: str
    app_secret: str
    app_token: str
    table_id: str
    admin_open_id: str = ""


# ============ API 路由 ============

@app.get("/")
async def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.post("/api/process")
async def process_text(req: ProcessTextRequest):
    """处理文本：提取 + 校验"""
    try:
        result = master.process_text(req.text, req.app_type)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/confirm")
async def confirm_records(req: ConfirmRecordsRequest):
    """确认记录并写入飞书"""
    try:
        config = load_config()
        table_id = config.get("table_id", "")
        # 更新lark client配置
        lark_client.set_config(
            config.get("app_id", ""),
            config.get("app_secret", ""),
            config.get("app_token", ""),
        )
        result = master.confirm_records(req.records, table_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/report")
async def get_report():
    """生成汇报"""
    try:
        result = master.generate_report()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/config")
async def get_config():
    """获取配置"""
    config = load_config()
    # 不返回secret
    return {
        "app_id": config.get("app_id", ""),
        "app_token": config.get("app_token", ""),
        "table_id": config.get("table_id", ""),
        "admin_open_id": config.get("admin_open_id", ""),
    }


@app.post("/api/config")
async def save_lark_config(req: LarkConfigRequest):
    """保存飞书配置"""
    global lark_bot
    
    config = load_config()
    config.update({
        "app_id": req.app_id,
        "app_secret": req.app_secret,
        "app_token": req.app_token,
        "table_id": req.table_id,
        "admin_open_id": req.admin_open_id,
    })
    save_config(config)

    # 更新 lark client
    lark_client.set_config(req.app_id, req.app_secret, req.app_token)
    
    # 自动启动飞书机器人（长连接）
    try:
        from lark_bot import LarkBot
        lark_bot = LarkBot(req.app_id, req.app_secret, master)
        lark_bot.start()
        bot_status = "飞书机器人已启动"
    except Exception as e:
        bot_status = f"飞书机器人启动失败: {e}"

    return {"success": True, "message": f"配置已保存，{bot_status}"}


@app.post("/api/lark/test")
async def test_lark():
    """测试飞书连接"""
    config = load_config()
    lark_client.set_config(
        config.get("app_id", ""),
        config.get("app_secret", ""),
        config.get("app_token", ""),
    )
    result = lark_client.test_connection()
    return result


@app.get("/api/lark/tables")
async def list_lark_tables():
    """列出飞书表格"""
    config = load_config()
    lark_client.set_config(
        config.get("app_id", ""),
        config.get("app_secret", ""),
        config.get("app_token", ""),
    )
    try:
        tables = lark_client.list_tables()
        return {"success": True, "tables": tables}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.get("/api/records")
async def get_records():
    """获取已确认的记录"""
    return {"records": master.confirmed_records}


@app.get("/api/pending")
async def get_pending():
    """获取待确认的记录"""
    global lark_bot
    pending = []
    if lark_bot:
        for req_id, p in lark_bot.pending_records.items():
            pending.append({
                "request_id": req_id,
                "blogger_count": len(p.get("records", [])),
                "warnings": len(p.get("warnings", [])),
                "chat_id": p.get("chat_id", "")
            })
    return {"pending": pending}


# ============ 飞书机器人接口 ============

@app.post("/api/lark/bot/webhook")
async def lark_bot_webhook(request: dict):
    """
    飞书机器人事件回调接口
    接收群里@机器人的消息，自动处理
    """
    # 1. URL验证（飞书配置回调时的首次验证）
    if "challenge" in request:
        return {"challenge": request["challenge"]}

    # 2. 处理事件
    event = request.get("event", {})
    event_type = request.get("header", {}).get("event_type", "")

    if event_type == "im.message.receive_v1":
        # 收到消息事件
        message = event.get("message", {})
        sender = event.get("sender", {})

        # 只处理@机器人的消息
        mentions = message.get("mentions", [])
        if not mentions:
            return {"success": True, "message": "非@消息，忽略"}

        # 提取消息文本
        content = message.get("content", "{}")
        try:
            content_json = json.loads(content)
            text = content_json.get("text", "")
        except:
            text = content

        # 去掉@机器人的部分
        for mention in mentions:
            bot_name = mention.get("name", "")
            text = text.replace(f"@{bot_name}", "").strip()

        if not text:
            return {"success": True, "message": "空消息，忽略"}

        # 获取聊天ID，用于回复
        chat_id = message.get("chat_id", "")
        message_id = message.get("message_id", "")

        # 调用Master Agent处理
        try:
            result = master.process_text(text)

            # 生成回复消息
            reply_text = format_lark_reply(result)

            # 回复到群里
            send_lark_message(chat_id, reply_text)

            return {"success": True, "message": "已处理"}
        except Exception as e:
            send_lark_message(chat_id, f"处理失败：{str(e)}")
            return {"success": False, "error": str(e)}

    return {"success": True}


def format_lark_reply(result: dict) -> str:
    """把处理结果格式化成飞书消息文本"""
    lines = []
    lines.append("📊 申请处理结果")
    lines.append("")

    extracted = result.get("extracted", {})
    summary = extracted.get("summary", {})
    validation = result.get("validation", {})

    # 基本信息
    lines.append(f"🎵 歌曲：{summary.get('song_name', '未知')}")
    lines.append(f"💰 总金额：¥{summary.get('total_amount', 0)}")
    lines.append(f"📝 明细数：{summary.get('detail_count', 0)} 条")
    lines.append("")

    # 校验结果
    if validation.get("is_valid"):
        lines.append("✅ 校验通过")
    else:
        lines.append("❌ 校验有错误：")
        for e in validation.get("errors", []):
            lines.append(f"  - {e.get('message', '')}")

    if validation.get("warnings"):
        lines.append("")
        lines.append("⚠️ 警告：")
        for w in validation.get("warnings", []):
            lines.append(f"  - {w.get('message', '')}")

    return "\n".join(lines)


def send_lark_message(chat_id: str, text: str):
    """发送消息到飞书群"""
    config = load_config()
    lark_client.set_config(
        config.get("app_id", ""),
        config.get("app_secret", ""),
        config.get("app_token", ""),
    )
    lark_client.send_message(chat_id, text)


if __name__ == "__main__":
    import uvicorn
    
    # 自动启动飞书机器人
    auto_start_bot()
    
    uvicorn.run(app, host="0.0.0.0", port=8889)
