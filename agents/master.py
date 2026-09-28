"""
Master Agent
主 Agent：协调调度各子 Agent
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from extractor import ExtractorAgent
from validator import ValidatorAgent, ValidationResult
from reporter import ReporterAgent
from lark_client import LarkClient
from llm_client import LLMClient
from typing import List, Dict, Optional
import json
import time
import hashlib

# 幂等：pending 状态超过该时长（秒）视为过期，允许重新提交
PENDING_EXPIRE_SECONDS = 30 * 60


class MasterAgent:
    """主 Agent：推广申请处理协调者"""

    # 数据文件路径（类属性，便于测试隔离替换）
    records_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "records.json")
    processed_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "processed_messages.json")

    def __init__(self, lark_client: Optional[LarkClient] = None, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client
        self.extractor = ExtractorAgent(use_llm=True, llm_client=llm_client)
        self.validator = ValidatorAgent()
        self.reporter = ReporterAgent()
        self.lark = lark_client or LarkClient()

        # 会话状态
        self.extracted_data = None  # 当前提取的数据
        self.validation_result = None  # 当前校验结果
        self.confirmed_records = []  # 已确认的记录
        self.known_songs = ["你是我的仰望"]  # 已知歌名，自动积累
        
        # 持久化：从文件加载历史记录
        self.records_file = MasterAgent.records_file
        self._load_records()
        # 幂等：已处理/处理中的消息指纹（持久化，重启不丢）
        self.processed_file = MasterAgent.processed_file
        self._processed = self._load_processed()
        # 当前处理中的消息指纹（process_text → confirm/reject 关联）
        self._current_hash = None
    
    def _load_records(self):
        """从文件加载历史记录，自动去重"""
        try:
            if os.path.exists(self.records_file):
                with open(self.records_file, 'r', encoding='utf-8') as f:
                    raw_records = json.load(f)
                # 自动去重：按博主+类型+金额去重
                seen = set()
                unique_records = []
                for r in raw_records:
                    # 给旧记录补date字段
                    if not r.get("date") and r.get("apply_date"):
                        r["date"] = r["apply_date"]
                    key = (r.get("blogger_name"), r.get("app_type"), r.get("amount"))
                    if key not in seen:
                        seen.add(key)
                        unique_records.append(r)
                self.confirmed_records = unique_records
                # 去重后重新保存
                self._save_records()
                print(f"[Master] 加载了 {len(self.confirmed_records)} 条历史记录（已去重）")
        except Exception as e:
            print(f"[Master] 加载历史记录失败: {e}")
    
    def _save_records(self):
        """保存历史记录到文件"""
        try:
            os.makedirs(os.path.dirname(self.records_file), exist_ok=True)
            with open(self.records_file, 'w', encoding='utf-8') as f:
                json.dump(self.confirmed_records, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Master] 保存历史记录失败: {e}")

    # ==================== 幂等防重 ====================

    def _load_processed(self) -> dict:
        """加载消息指纹记录 {hash: {status, time, summary}}"""
        try:
            if os.path.exists(self.processed_file):
                with open(self.processed_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"[Master] 加载消息指纹失败: {e}")
        return {}

    def _save_processed(self):
        """持久化消息指纹记录"""
        try:
            os.makedirs(os.path.dirname(self.processed_file), exist_ok=True)
            with open(self.processed_file, 'w', encoding='utf-8') as f:
                json.dump(self._processed, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Master] 保存消息指纹失败: {e}")

    @staticmethod
    def _text_hash(text: str) -> str:
        """消息文本指纹（去首尾空白后 MD5）"""
        return hashlib.md5(text.strip().encode("utf-8")).hexdigest()

    def check_duplicate(self, text: str) -> Optional[dict]:
        """
        幂等检查：同一段文本重复提交时返回命中信息。
        返回 None 表示可正常处理；否则返回 {"duplicate": True, "message": ...}。
        """
        h = self._text_hash(text)
        rec = self._processed.get(h)
        if not rec:
            return None
        if rec.get("status") == "confirmed":
            return {
                "duplicate": True,
                "message": f"该申请已于 {rec.get('time', '')} 处理并写入表格，请勿重复提交。",
            }
        if rec.get("status") == "pending":
            # 超过 30 分钟的待确认视为过期，允许重新提交
            if time.time() - rec.get("time", 0) > PENDING_EXPIRE_SECONDS:
                del self._processed[h]
                self._save_processed()
                return None
            return {
                "duplicate": True,
                "message": "该申请正在等待确认，请先处理上方确认卡片（确认写入或驳回）。",
            }
        return None

    def mark_processed(self, text: str, status: str = "pending", summary: str = ""):
        """记录消息已进入处理流程（pending=待确认 / confirmed=已写入 / rejected=已驳回）"""
        h = self._text_hash(text)
        self._processed[h] = {
            "status": status,
            "time": time.time(),
            "summary": summary,
        }
        self._current_hash = h
        self._save_processed()

    def mark_confirmed_hash(self, h: str):
        """确认卡片点击「确认写入」成功后，把消息指纹置为 confirmed"""
        if h and h in self._processed:
            self._processed[h]["status"] = "confirmed"
            self._processed[h]["time"] = time.time()
            self._save_processed()

    def mark_rejected_hash(self, h: str):
        """确认卡片点击「驳回」后，移除消息指纹（允许修改后重新提交）"""
        if h and h in self._processed:
            del self._processed[h]
            self._save_processed()

    def process_text(self, text: str, app_type: str = "auto") -> dict:
        """
        主流程：处理一段申请文本
        1. 幂等检查：同一段文本重复提交直接返回，不重复提取/校验
        2. 提取 Agent 提取结构化数据
        3. 校验 Agent 校验数据
        返回提取和校验结果，等待人工确认
        """
        # 幂等：重复提交直接拦截（不调用 LLM，节省成本）
        dup = self.check_duplicate(text)
        if dup:
            print(f"[Master] 幂等拦截重复提交: {dup['message']}")
            return {
                "duplicate": True,
                "message": dup["message"],
                "extracted": None,
                "validation": {"is_valid": False, "errors": [], "warnings": []},
            }

        print(f"[Master] 开始处理申请文本...")

        # Step 1: 提取
        print("[Master] 调用提取 Agent...")
        extracted = self.extractor.extract_with_fallback(text, app_type)
        self.extracted_data = extracted
        print(f"[Master] 提取完成，识别到 {len(extracted['details'])} 条明细")
        print(f"[Master] 提取到的summary: {extracted['summary']}")

        # Step 2: 校验
        print("[Master] 调用校验 Agent...")
        
        # 先从飞书拉取已有博主和歌名，用于笔误检查
        try:
            import os
            config_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "config.json")
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    config = json.load(f)
                table_id = config.get("table_id", "")
                if table_id:
                    # 拉取已有记录
                    token = self.lark._get_tenant_token()
                    base_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.lark.app_token}/tables/{table_id}/records?page_size=100"
                    import requests
                    resp = requests.get(base_url, headers={"Authorization": f"Bearer {token}"})
                    existing = resp.json().get("data", {}).get("items", [])
                    existing_bloggers = [rec.get("fields", {}).get("抖音昵称", "") for rec in existing if rec.get("fields", {}).get("抖音昵称")]
                    existing_songs = []  # 表格里没有歌曲名字段，先空着
                    self.validator.set_existing_data(existing_bloggers, self.known_songs)
                    print(f"[Master] 已加载 {len(existing_bloggers)} 个已有博主用于笔误检查")
        except Exception as e:
            print(f"[Master] 加载已有博主失败: {e}")
        
        validation = self.validator.validate(extracted)
        self.validation_result = validation
        print(f"[Master] 校验完成：{'通过' if validation.is_valid else '有错误'}")

        # 标记消息为待确认（幂等），确认/驳回时更新状态
        self.mark_processed(
            text,
            status="pending",
            summary=json.dumps(extracted.get("summary", {}), ensure_ascii=False)[:200],
        )

        return {
            "extracted": extracted,
            "validation": {
                "is_valid": validation.is_valid,
                "errors": validation.errors,
                "warnings": validation.warnings,
            },
            # 幂等：消息指纹，供确认/驳回卡片回调时精确定位
            "request_hash": self._current_hash,
        }

    def confirm_records(self, records: List[dict], table_id: str = "") -> dict:
        """
        人工确认后的记录，写入飞书表格
        """
        print(f"[Master] 确认 {len(records)} 条记录，开始写入飞书...")

        # 写入飞书
        write_result = self.write_to_lark(records, table_id)
        if write_result["success"]:
            # 去重：避免重复提交导致记录重复、金额翻倍
            new_records = []
            existing_keys = set((r.get("blogger_name"), r.get("app_type"), r.get("amount")) for r in self.confirmed_records)
            for r in records:
                # 给每条记录加上date字段（和前端字段名对齐）
                r["date"] = r.get("apply_date", "")
                key = (r.get("blogger_name"), r.get("app_type"), r.get("amount"))
                if key not in existing_keys:
                    new_records.append(r)
                    existing_keys.add(key)
            
            self.confirmed_records.extend(new_records)
            # 把这次的歌名加到已知列表
            if self.extracted_data and self.extracted_data.get("summary", {}).get("song_name"):
                song = self.extracted_data["summary"]["song_name"]
                if song and song not in self.known_songs:
                    self.known_songs.append(song)
            # 持久化保存
            self._save_records()
            # 幂等：确认成功 → 消息指纹置为 confirmed（防止同文本再次提交）
            self.mark_confirmed_hash(self._current_hash)
            print(f"[Master] 写入成功，共 {len(records)} 条")
        else:
            print(f"[Master] 写入失败：{write_result['message']}")

        return write_result

    def write_to_lark(self, records: List[dict], table_id: str = "") -> dict:
        """
        按博主维度合并数据，更新飞书表格
        逻辑：查找已有博主，更新抖加、支付人、打款状态等字段
        """
        try:
            if not table_id:
                return {"success": False, "message": "未配置表格ID"}

            # 第一步：按博主名合并数据
            blogger_map = {}
            for r in records:
                blogger = r.get("blogger_name", "")
                if not blogger:
                    continue

                if blogger not in blogger_map:
                    blogger_map[blogger] = {
                        "抖加": 0,
                        "抖加支付人": "",
                        "是否打款": "否",
                        "打款人": "",
                        "打款日期": "",
                        "备注": "",
                    }

                # 抖加数据
                if r.get("app_type") == "douyin_ad":
                    blogger_map[blogger]["抖加"] += r.get("amount", 0)
                    blogger_map[blogger]["抖加支付人"] = r.get("payer", "")
                    if r.get("_has_modify"):
                        blogger_map[blogger]["_has_modify"] = True

                # 垫付数据
                if r.get("app_type") == "advance_payment":
                    blogger_map[blogger]["是否打款"] = "是"
                    blogger_map[blogger]["打款人"] = r.get("payer", "")
                    # 打款日期就是垫付的日期
                    blogger_map[blogger]["打款日期"] = r.get("date", "2026-09-03")
                    if r.get("_has_modify"):
                        blogger_map[blogger]["_has_modify"] = True
            
            # 把校验发现的异常，加到对应类型的博主备注里
            if self.validation_result:
                warnings = self.validation_result.warnings
                if warnings:
                    warning_text = "；".join([w.get("message", "") for w in warnings])
                    # 判断异常是哪类的
                    warning_is_advance = any("垫付" in w.get("message", "") for w in warnings)
                    warning_is_douyin = any("抖加" in w.get("message", "") for w in warnings)
                    
                    for blogger, data in blogger_map.items():
                        # 这个博主是哪类的
                        has_douyin = data.get("抖加", 0) > 0
                        has_advance = data.get("打款人", "") != ""
                        
                        # 对应类型有异常，就加备注和异常标记
                        if (has_douyin and warning_is_douyin) or (has_advance and warning_is_advance):
                            if data["备注"]:
                                data["备注"] += f"；⚠️ {warning_text}"
                            else:
                                data["备注"] = f"⚠️ {warning_text}"
                            data["_has_warning"] = True
                
                # 合并备注
                if r.get("备注"):
                    if blogger_map[blogger]["备注"]:
                        blogger_map[blogger]["备注"] += "；" + r["备注"]
                    else:
                        blogger_map[blogger]["备注"] = r["备注"]

            # 第二步：查找表格里已有哪些博主，更新对应行
            import requests
            token = self.lark._get_tenant_token()
            base_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.lark.app_token}/tables/{table_id}"

            # 获取所有记录
            resp = requests.get(
                f"{base_url}/records?page_size=100",
                headers={"Authorization": f"Bearer {token}"}
            )
            existing_records = resp.json().get("data", {}).get("items", [])

            # 建立博主名 → record_id 的映射
            blogger_record_map = {}
            for rec in existing_records:
                name = rec.get("fields", {}).get("抖音昵称", "")
                if name:
                    blogger_record_map[name] = rec["record_id"]

            # 第三步：更新或新建
            updated = 0
            created = 0
            for blogger, data in blogger_map.items():
                fields = {
                    "抖加": str(data["抖加"]) if data["抖加"] else "",
                    "抖加支付人": data["抖加支付人"],
                    "是否打款": data["是否打款"],
                    "打款人": data["打款人"],
                }

                # 如果有异常，写到备注字段，不碰审核结果
                if data.get("备注"):
                    fields["备注"] = data["备注"]

                if blogger in blogger_record_map:
                    # 更新已有记录：只有本次有数据的字段才更新，不覆盖已有值
                    record_id = blogger_record_map[blogger]
                    update_fields = {}
                    
                    # 抖加字段：只有本次有抖加数据才更新
                    if data["抖加"] > 0:
                        update_fields["抖加"] = str(data["抖加"])
                        update_fields["抖加支付人"] = data["抖加支付人"]
                    
                    # 打款字段：只有本次有打款数据才更新
                    if data["打款人"]:
                        update_fields["是否打款"] = data["是否打款"]
                        update_fields["打款人"] = data["打款人"]
                        # 打款日期是时间戳字段，需要转成毫秒时间戳
                        if data["打款日期"]:
                            import time
                            try:
                                ts = int(time.mktime(time.strptime(data["打款日期"], "%Y-%m-%d"))) * 1000
                                update_fields["打款日期"] = ts
                            except:
                                pass
                    
                    # 备注和审核结果：
                    # 有修改 → 人工修改
                    # 有异常 → 异常待确认
                    # 都没有 → 留空
                    if data.get("备注"):
                        update_fields["备注"] = data["备注"]
                        if data.get("_has_modify"):
                            update_fields["审核结果"] = "人工修改"
                        elif data.get("_has_warning"):
                            update_fields["审核结果"] = "异常待确认"
                    resp = requests.put(
                        f"{base_url}/records/{record_id}",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        json={"fields": update_fields}
                    )
                    if resp.json().get("code") == 0:
                        updated += 1
                    else:
                        print(f"更新 {blogger} 失败: {resp.json().get('msg')}")
                else:
                    # 新建记录
                    fields["抖音昵称"] = blogger
                    requests.post(
                        f"{base_url}/records",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        json={"fields": fields}
                    )
                    created += 1

            return {
                "success": True,
                "message": f"更新 {updated} 条，新建 {created} 条博主记录",
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"写入失败：{str(e)}",
            }

    def generate_report(self) -> dict:
        """生成汇报"""
        print("[Master] 生成汇报...")
        report = self.reporter.generate_report(self.confirmed_records)
        text_report = self.reporter.generate_text_report(report)
        return {
            "report": report,
            "text": text_report,
        }

    def process_complete_flow(self, text: str, table_id: str = "") -> dict:
        """
        完整流程：提取 → 校验 → 写入 → 汇报
        （用于演示一键完成）
        """
        # 1. 提取 + 校验
        result = self.process_text(text)

        # 2. 如果校验通过，自动写入
        write_result = {"success": False, "message": "未执行写入"}
        if result["validation"]["is_valid"]:
            records = result["extracted"]["details"]
            write_result = self.confirm_records(records)

        # 3. 生成汇报
        report = self.generate_report()

        return {
            **result,
            "write_result": write_result,
            "report": report,
        }
