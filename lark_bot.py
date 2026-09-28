"""
飞书机器人 - 长连接模式
本地开发直接用，不需要公网地址
"""
import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from lark_oapi.api.cardkit.v1 import *
import json
import threading
import os

# 导入我们的Master Agent
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from agents.master import MasterAgent
from lark_client import LarkClient


class LarkBot:
    """飞书机器人（长连接模式）"""

    def __init__(self, app_id: str, app_secret: str, master: MasterAgent):
        self.app_id = app_id
        self.app_secret = app_secret
        self.master = master
        self.client = None
        self._running = False
        # 待确认的记录缓存：message_id -> records
        self.pending_records = {}
        self.pending_card_ids = {}

    def start(self):
        """启动机器人（长连接模式）"""
        if self._running:
            return

        # 创建事件处理器
        event_handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(self._on_message_receive) \
            .register_p2_card_action_trigger(self._on_card_action) \
            .build()

        # 创建长连接客户端
        self.client = lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        # 在后台线程启动
        def run():
            import asyncio
            # 在子线程里创建新的事件循环
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            self._running = True
            try:
                self.client.start()
            finally:
                self._running = False
                print("⚠️ 飞书机器人已断开")

        threading.Thread(target=run, daemon=True).start()
        print("✅ 飞书机器人已启动（长连接模式）")

        # 启动监控线程，自动重启
        retry_count = 0
        def monitor():
            nonlocal retry_count
            import time
            while True:
                time.sleep(10)  # 每10秒检查一次
                if not self._running:
                    retry_count += 1
                    print(f"🔄 检测到机器人断开，第{retry_count}次尝试自动重启...")
                    try:
                        self.start()
                        retry_count = 0  # 重启成功，重置计数
                    except Exception as e:
                        print(f"❌ 机器人重启失败: {e}")
                        # 重启3次都失败，发告警
                        if retry_count >= 3:
                            self._send_alert("⚠️ 飞书机器人已断开，连续重启3次失败，请检查配置和网络！")
                            retry_count = 0  # 重置，避免重复告警

        threading.Thread(target=monitor, daemon=True).start()
    
    def restart(self):
        """手动重启机器人连接"""
        print("🔄 手动重启机器人...")
        self._running = False
        if self.client:
            try:
                self.client.close()
            except:
                pass
        import time
        time.sleep(1)
        self.start()
        return True
    
    def status(self):
        """返回机器人状态"""
        return {
            "running": self._running,
            "pending_count": len(self.pending_records)
        }

    def _on_message_receive(self, data: P2ImMessageReceiveV1):
        """
        收到群消息的回调
        """
        try:
            message = data.event.message
            sender = data.event.sender

            # 提取消息文本
            content = json.loads(message.content)
            msg_type = message.message_type
            
            # 调试：打印原始content结构
            print(f"   原始content: {json.dumps(content, ensure_ascii=False)[:500]}")
            
            text = ""
            if msg_type == "text":
                text = content.get("text", "")
            elif msg_type == "post":
                # 富文本（代码块、格式消息）
                # 兼容两种结构：直接{title, content} 或 包在post.zh_cn里
                post = content.get("post", content)
                
                # 找到实际的内容对象
                content_obj = post
                if "content" not in content_obj and "zh_cn" in post:
                    content_obj = post["zh_cn"]
                
                lines = []
                # 标题
                if content_obj.get("title"):
                    lines.append(content_obj["title"])
                
                # 内容段落
                for para in content_obj.get("content", []):
                    para_text = ""
                    for element in para:
                        tag = element.get("tag")
                        if tag == "text":
                            para_text += element.get("text", "")
                        elif tag == "code_block":
                            para_text += element.get("text", "")
                        elif tag == "a":
                            para_text += element.get("text", "")
                    if para_text.strip():
                        lines.append(para_text)
                text = "\n".join(lines)

            print(f"📩 收到原始消息: {text[:200]}...")
            print(f"   消息类型: {msg_type}, mentions: {message.mentions}")

            # 只处理@机器人的消息
            mentions = message.mentions
            if not mentions:
                print("   没有@机器人，忽略")
                return

            # 调用Master Agent处理
            result = self.master.process_text(text)

            # 幂等：重复提交直接回复，不再发确认卡片
            if result.get("duplicate"):
                self._send_message(message.chat_id, f"♻️ {result.get('message', '该申请已处理过')}")
                print(f"   幂等拦截重复提交: {result.get('message')}")
                return

            # 生成request_id
            import time
            request_id = f"req_{int(time.time()*1000)}"

            # 保存待确认的记录
            if result.get("extracted", {}).get("details"):
                # 生成模板变量
                variables = self._build_template_vars(result, request_id)
                
                self.pending_records[request_id] = {
                    "records": result["extracted"]["details"],
                    "chat_id": message.chat_id,
                    "variables": variables,
                    "warnings": result.get("validation", {}).get("warnings", []) + result.get("validation", {}).get("errors", []),
                    "request_hash": result.get("request_hash", ""),  # 幂等指纹
                }

            # 用模板发送卡片
            card_id = self._send_template_card(message.chat_id, variables)
            
            # 有异常的话，额外发一条告警提醒
            warnings = result.get("validation", {}).get("warnings", []) + result.get("validation", {}).get("errors", [])
            if warnings:
                # 读配置里的管理员ID
                import os
                config_file = os.path.join(os.path.dirname(__file__), "data", "config.json")
                admin_open_id = ""
                if os.path.exists(config_file):
                    with open(config_file, 'r') as f:
                        config = json.load(f)
                    admin_open_id = config.get("admin_open_id", "")
                
                alert_text = ""
                if admin_open_id:
                    alert_text += f'<at user_id="{admin_open_id}"></at> '
                alert_text += f"⚠️ **发现 {len(warnings)} 个异常问题，请人工确认！**\n"
                for w in warnings:
                    alert_text += f"· {w.get('message', '')}\n"
                
                # 发富文本消息，支持@人
                self._send_rich_message(message.chat_id, alert_text)
            
            # 把card_id存到pending_records里
            if request_id in self.pending_records:
                self.pending_records[request_id]["card_id"] = card_id

            print("✅ 已发送确认卡片")

        except Exception as e:
            print(f"❌ 处理消息失败: {e}")
            import traceback
            traceback.print_exc()

    def _build_template_vars(self, result: dict, request_id: str) -> dict:
        """构建模板变量"""
        extracted = result.get("extracted", {})
        summary = extracted.get("summary", {})
        validation = result.get("validation", {})
        details = extracted.get("details", [])
        
        app_type = extracted.get("app_type", "mixed")
        
        # 博主明细
        blogger_lines = []
        bloggers = set()
        for d in details:
            name = d.get("blogger_name", "")
            amount = d.get("amount", 0)
            type_name = "抖加" if d.get("app_type") == "douyin_ad" else "垫付"
            blogger_lines.append(f"· {name}：{type_name}¥{amount}")
            bloggers.add(name)
        
        blogger_list = "\n".join(blogger_lines)
        
        # 问题列表
        warnings = validation.get("errors", []) + validation.get("warnings", [])
        warning_lines = [f"⚠️ {w.get('message', '')}" for w in warnings]
        warning_list = "\n".join(warning_lines) if warning_lines else "✅ 数据校验通过"
        
        # 构建汇总行：有什么显示什么
        has_douyin = app_type in ["douyin_ad", "mixed"] and summary.get("douyin_total", 0) > 0
        has_advance = app_type in ["advance_payment", "mixed"] and summary.get("advance_total", 0) > 0
        
        summary_lines = []
        if has_douyin:
            summary_lines.append(f"🎵 抖加总额：¥{int(summary.get('douyin_total', 0))}")
        if has_advance:
            summary_lines.append(f"💰 垫付总额：¥{int(summary.get('advance_total', 0))}")
        
        variables = {
            "summary_text": "\n".join(summary_lines),
            "blogger_count": len(bloggers),
            "warning_count": len(warnings),
            "warning_list": warning_list,
            "blogger_list": blogger_list,
            "request_id": request_id
        }
        return variables

    def _build_confirm_card(self, result: dict, request_id: str) -> dict:
        """构建确认卡片"""
        extracted = result.get("extracted", {})
        summary = extracted.get("summary", {})
        validation = result.get("validation", {})
        details = extracted.get("details", [])

        # 按博主汇总
        bloggers = {}
        for d in details:
            name = d.get("blogger_name", "")
            if name not in bloggers:
                bloggers[name] = {"douyin": 0, "advance": 0}
            if d.get("app_type") == "douyin_ad":
                bloggers[name]["douyin"] += d.get("amount", 0)
            else:
                bloggers[name]["advance"] += d.get("amount", 0)

        # 构建卡片元素
        elements = []

        # 头部信息
        # 根据类型只显示有数据的汇总
        app_type = result.get("extracted", {}).get("app_type", "mixed")
        summary_lines = []
        if app_type in ["douyin_ad", "mixed"]:
            summary_lines.append(f"**🎵 抖加总额：** ¥{summary.get('douyin_total', 0)}")
        if app_type in ["advance_payment", "mixed"]:
            summary_lines.append(f"**💰 垫付总额：** ¥{summary.get('advance_total', 0)}")
        summary_lines.append(f"**📝 涉及博主：** {len(bloggers)} 位")
        
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "\n".join(summary_lines)
            }
        })

        elements.append({"tag": "hr"})

        # 校验结果
        if not validation.get("is_valid") or validation.get("warnings"):
            errors = validation.get("errors", []) + validation.get("warnings", [])
            if errors:
                err_text = "\n".join([f"⚠️ {e.get('message', '')}" for e in errors])
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**发现 {len(errors)} 个问题：**\n{err_text}"
                    }
                })
                elements.append({"tag": "hr"})

        # 博主明细
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "**📋 博主明细：**"}
        })

        detail_lines = []
        for name, data in bloggers.items():
            parts = []
            if data["douyin"] > 0:
                parts.append(f"抖加¥{data['douyin']}")
            if data["advance"] > 0:
                parts.append(f"垫付¥{data['advance']}")
            detail_lines.append(f"· {name}：{' + '.join(parts)}")

        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "\n".join(detail_lines[:10])
            }
        })

        elements.append({"tag": "hr"})

        # 修改意见输入框
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "**✏️ 修改意见（选填）：**"}
        })

        elements.append({
            "tag": "input",
            "name": "modify_note",
            "placeholder": "如果数据有误，请在这里输入修改内容，比如：初秋的抖加改成350",
            "default": ""
        })

        elements.append({"tag": "hr"})

        # 按钮（带上request_id）
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "✅ 确认写入"},
                    "type": "primary",
                    "value": {"action": "confirm", "request_id": request_id}
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "❌ 驳回"},
                    "type": "danger",
                    "value": {"action": "reject", "request_id": request_id}
                }
            ]
        })

        # 构建完整卡片
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "📊 收到新的推广申请"},
                "template": "blue"
            },
            "elements": elements
        }

        return card

    def _on_card_action(self, data):
        """处理卡片按钮点击事件"""
        try:
            # 获取回调数据
            action = data.event.action
            value = action.value
            # 如果value是字符串，解析成字典
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except:
                    value = None
            
            # 表单提交事件（点修改意见的发送按钮）
            if not value:
                print(f"\n📝 表单提交：修改意见")
                print(f"   form_value: {action.form_value}")
                print(f"   input_value: {action.input_value}")
                print(f"   action.name: {action.name}")
                
                # 把修改意见存到pending里
                modify_note = action.input_value if isinstance(action.input_value, str) else ""
                for req_id, pending in self.pending_records.items():
                    if pending["chat_id"] == data.event.context.open_chat_id:
                        pending["modify_note"] = modify_note
                        print(f"   已保存修改意见：{modify_note}")
                        self._send_message(data.event.context.open_chat_id, "✅ 已保存修改意见，点「确认写入」提交")
                        break
                return
            
            action_type = value.get("action") if value else None
            request_id = value.get("request_id") if value else None

            print(f"\n🔘 卡片点击：{action_type}, request_id: {request_id}")
            print(f"   event.context属性: {[x for x in dir(data.event.context) if not x.startswith('_')]}")
            print(f"   action属性: {[x for x in dir(action) if not x.startswith('_')]}")
            print(f"   action.value: {action.value}")

            # 从待确认记录里找
            if request_id not in self.pending_records:
                print("⚠️ 找不到对应的待确认记录（可能服务重启过）")
                # 回复用户
                try:
                    self._send_message(data.event.context.open_message_id, "⚠️ 记录已过期，请重新发送申请文本")
                except:
                    pass
                return

            pending = self.pending_records[request_id]
            records = pending["records"]
            chat_id = pending["chat_id"]
            
            # 把处理逻辑放到后台线程，立刻返回，避免飞书回调超时
            def do_process():
                try:
                    if action_type == "confirm":
                        # 从pending里拿之前保存的修改意见
                        modify_note = pending.get("modify_note", "")
                        
                        # 判断修改意见是针对哪部分的
                        note_lower = modify_note.lower() if modify_note else ""
                        note_about_douyin = "抖加" in note_lower
                        note_about_advance = "垫付" in note_lower
                        # 如果修改意见没说针对哪部分，就全局加
                        note_global = not note_about_douyin and not note_about_advance and bool(modify_note)

                        # 如果有修改意见，按类型加到对应记录里
                        if modify_note:
                            for r in records:
                                app_type = r.get("app_type", "")
                                # 只给对应类型的记录加备注
                                if (app_type == "douyin_ad" and (note_about_douyin or note_global)) or \
                                   (app_type == "advance_payment" and (note_about_advance or note_global)):
                                    r["备注"] = f"人工修改：{modify_note}"
                                    r["_has_modify"] = True

                        # 获取表格ID
                        config_file = os.path.join(os.path.dirname(__file__), "data", "config.json")
                        table_id = ""
                        if os.path.exists(config_file):
                            with open(config_file, 'r') as f:
                                config = json.load(f)
                            table_id = config.get("table_id", "")

                        # 写入表格
                        write_result = self.master.confirm_records(records, table_id)

                        # 幂等：确认成功 → 消息指纹置为 confirmed（防止同文本重复提交）
                        if write_result.get("success"):
                            self.master.mark_confirmed_hash(pending.get("request_hash", ""))

                        # 发送结果消息
                        reply_lines = []
                        if write_result.get("success"):
                            reply_lines.append("✅ **已确认，数据已写入飞书表格**")
                            reply_lines.append(write_result.get('message', ''))
                            
                            # 生成负责人简报
                            summary = self.master.extracted_data.get("summary", {})
                            douyin_total = summary.get("douyin_total", 0)
                            advance_total = summary.get("advance_total", 0)
                            
                            # 按博主汇总
                            blogger_summary = {}
                            for r in records:
                                name = r.get("blogger_name", "")
                                if name not in blogger_summary:
                                    blogger_summary[name] = {"douyin": 0, "advance": 0}
                                if r.get("app_type") == "douyin_ad":
                                    blogger_summary[name]["douyin"] += r.get("amount", 0)
                                else:
                                    blogger_summary[name]["advance"] += r.get("amount", 0)
                            
                            reply_lines.append("")
                            reply_lines.append("📊 **本次申请简报：**")
                            if douyin_total > 0:
                                reply_lines.append(f"- 🎵 抖加总额：¥{int(douyin_total)}")
                            if advance_total > 0:
                                reply_lines.append(f"- 💰 垫付总额：¥{int(advance_total)}")
                            reply_lines.append(f"- 👥 涉及博主明细：")
                            for name, amounts in blogger_summary.items():
                                parts = []
                                if amounts["douyin"] > 0:
                                    parts.append(f"抖加¥{int(amounts['douyin'])}")
                                if amounts["advance"] > 0:
                                    parts.append(f"垫付¥{int(amounts['advance'])}")
                                reply_lines.append(f"  · {name}：{'，'.join(parts)}")
                            
                            # 异常点
                            if pending.get("warnings"):
                                reply_lines.append(f"- ⚠️ 待确认问题明细：")
                                for w in pending["warnings"]:
                                    reply_lines.append(f"  · {w.get('message', '')}")
                            
                            # 人工修改
                            if modify_note:
                                reply_lines.append(f"- ✏️ 人工修改：{modify_note}")
                        else:
                            reply_lines.append("❌ **写入失败**")
                            reply_lines.append(write_result.get('message', ''))
                        
                        if modify_note:
                            reply_lines.append("")
                            reply_lines.append(f"✏️ 已应用修改：{modify_note}")

                        self._send_message(chat_id, "\n".join(reply_lines))

                    elif action_type == "reject":
                        # 幂等：驳回 → 移除消息指纹（允许修改后重新提交）
                        self.master.mark_rejected_hash(pending.get("request_hash", ""))
                        self._send_message(chat_id, "❌ **已驳回，未写入表格**\n管理员已驳回本次申请，数据未写入表格。")
                finally:
                    # 删除待确认记录
                    if request_id in self.pending_records:
                        del self.pending_records[request_id]
            
            threading.Thread(target=do_process, daemon=True).start()
            
            # 立刻发一条处理中提示，体验更好
            if action_type == "confirm":
                self._send_message(chat_id, "⏳ 正在写入飞书表格，请稍候...")

        except Exception as e:
            print(f"❌ 处理卡片回调失败: {e}")
            import traceback
            traceback.print_exc()

    def _send_template_card(self, chat_id: str, variables: dict) -> str:
        """用模板卡片发送消息，返回message_id"""
        import requests
        token = self._get_token()
        
        template_id = "AAqT6ntJgcSKo"
        template_version = "1.0.4"
        
        url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        content = {
            "type": "template",
            "data": {
                "template_id": template_id,
                "template_version_name": template_version,
                "template_variable": variables
            }
        }
        body = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(content)
        }
        resp = requests.post(url, headers=headers, json=body)
        data = resp.json()
        print(f"   发送模板卡片返回: code={data.get('code')}, msg={data.get('msg')}")
        if data.get("code") == 0:
            message_id = data["data"]["message_id"]
            
            # 查询消息详情，拿到card_id
            get_msg_url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}"
            resp2 = requests.get(get_msg_url, headers=headers)
            msg_data = resp2.json()
            print(f"   查询消息详情返回: {msg_data}")
            
            # 从content里解析card_id
            card_id = ""
            try:
                content_str = msg_data["data"]["items"][0]["body"]["content"]
                content = json.loads(content_str)
                card_id = content.get("data", {}).get("card_id", "")
            except Exception as e:
                print(f"   解析card_id失败: {e}")
            
            print(f"   card_id: {card_id}")
            return card_id
        return ""

    def _update_template_card(self, card_id: str, variables: dict):
        """更新模板卡片"""
        import requests
        token = self._get_token()
        url = f"https://open.feishu.cn/open-apis/cardkit/v1/cards/{card_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        body = {
            "type": "template",
            "data": {
                "template_id": "AAqT6ntJgcSKo",
                "template_version_name": "1.0.3",
                "template_variable": variables
            }
        }
        resp = requests.put(url, headers=headers, json=body)
        data = resp.json()
        print(f"   更新模板卡片返回: code={data.get('code')}, msg={data.get('msg')}")
        return data.get("code") == 0
        """更新卡片内容"""
        import requests
        token = self._get_token()
        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        # 更新卡片接口直接传card对象，不需要包content
        body = card
        resp = requests.put(url, headers=headers, json=body)
        data = resp.json()
        print(f"   更新卡片返回: code={data.get('code')}, msg={data.get('msg')}")
        return data.get("code") == 0

    def _get_token(self) -> str:
        """获取tenant token"""
        lark_client = LarkClient()
        import json
        config_file = os.path.join(os.path.dirname(__file__), "data", "config.json")
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                config = json.load(f)
            lark_client.set_config(
                config.get("app_id", self.app_id),
                config.get("app_secret", self.app_secret),
                config.get("app_token", ""),
            )
        return lark_client._get_tenant_token()

    def _send_alert(self, text: str):
        """系统告警（异常已经在确认卡片上展示了，这里只记日志）"""
        print(f"🚨 系统告警：{text}")

    def _send_message(self, chat_id: str, text: str):
        """发送消息到群里"""
        # 使用飞书SDK发消息
        req = (CreateMessageRequest.builder()
               .receive_id_type("chat_id")
               .request_body(
                   CreateMessageRequestBody.builder()
                   .receive_id(chat_id)
                   .msg_type("text")
                   .content(json.dumps({"text": text}))
                   .build()
               )
               .build())

        # 获取tenant token
        lark_client = LarkClient()
        # 从配置文件读取
        config_file = os.path.join(os.path.dirname(__file__), "data", "config.json")
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                config = json.load(f)
            lark_client.set_config(
                config.get("app_id", self.app_id),
                config.get("app_secret", self.app_secret),
                config.get("app_token", ""),
            )

        # 调用飞书API发消息
        import requests
        token = lark_client._get_tenant_token()
        url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        body = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text})
        }
        requests.post(url, headers=headers, json=body)
    
    def _send_rich_message(self, chat_id: str, text: str):
        """发送富文本消息（支持@人）"""
        import requests
        lark_client = LarkClient()
        config_file = os.path.join(os.path.dirname(__file__), "data", "config.json")
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                config = json.load(f)
            lark_client.set_config(
                config.get("app_id", self.app_id),
                config.get("app_secret", self.app_secret),
                config.get("app_token", ""),
            )
        
        token = lark_client._get_tenant_token()
        url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        # 用post类型消息，支持<at>标签
        content = {
            "zh_cn": {
                "title": "",
                "content": [
                    [
                        {
                            "tag": "text",
                            "text": text
                        }
                    ]
                ]
            }
        }
        body = {
            "receive_id": chat_id,
            "msg_type": "post",
            "content": json.dumps(content)
        }
        requests.post(url, headers=headers, json=body)
