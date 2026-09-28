"""
信息提取 Agent
从自然语言文本中提取结构化的推广申请数据
支持：抖加申请、垫付申请、混合文本
支持：规则提取 + LLM提取 两种模式
"""
import re
import json
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict


@dataclass
class Application:
    """一条申请记录"""
    app_type: str  # douyin_ad / advance_payment
    song_name: str  # 歌曲名
    blogger_name: str  # 博主名/达人名
    amount: float  # 金额
    payer: str  # 支付人
    apply_date: str  # 申请日期
    raw_text: str  # 原始文本
    status: str = "pending"  # pending / confirmed / rejected
    notes: str = ""  # 备注
    confidence: float = 1.0  # 置信度：规则提取=1.0，LLM提取=0.7


class ExtractorAgent:
    """信息提取 Agent"""
    
    # LLM提取用的Prompt模板
    LLM_EXTRACT_PROMPT = """你是一个信息提取专家。请从以下推广申请文本中提取结构化数据。

## 输出字段（必须严格使用这些字段名，禁止改名、禁止嵌套、禁止额外字段）
- app_type: 申请类型，只能是 "douyin_ad"(抖加) 或 "advance_payment"(垫付)
- song_name: 歌曲名（书名号《》里的内容）
- blogger_name: 博主名/达人名
- amount: 金额（数字，不带单位、不带"块/元/¥"等）
- payer: 支付人/打款人
- apply_date: 申请日期（格式：YYYY-MM-DD）

## 输出要求
- 只输出一个 JSON 数组，每个元素是一条明细，数组外不要包裹任何对象
- 每个字段都必须是上述名字，原样输出
- 如果找不到某个字段，填空字符串
- 不要输出任何其他内容（不要```json标记）

## 待提取文本
{text}

## 输出示例
```json
[
  {{"app_type": "douyin_ad", "song_name": "你是我的仰望", "blogger_name": "初秋", "amount": 300, "payer": "爆火音乐", "apply_date": "2026-09-02"}}
]
```
"""

    def __init__(self, use_llm: bool = False, llm_client=None):
        """
        初始化提取Agent
        :param use_llm: 是否使用LLM提取（规则失败时兜底）
        :param llm_client: LLM客户端（如果use_llm=True）
        """
        self.use_llm = use_llm
        self.llm_client = llm_client

        # 歌曲名模式（《》中）
        self.song_pattern = re.compile(r'《([^》]+)》')
        # 金额模式
        self.amount_pattern = re.compile(r'[¥￥]?(\d+(?:\.\d+)?)\s*元?')
        # 日期模式
        self.date_pattern = re.compile(
            r'(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})'
        )
        # 支付人模式
        self.payer_pattern = re.compile(
            r'(?:支付人|打款人|付款人)[：:]\s*([^\s，。、\n]+)'
        )

    def extract(self, text: str, app_type: str = "auto") -> dict:
        """
        从文本中提取申请信息
        支持混合文本（同时包含抖加和垫付）
        """
        warnings = []

        # 预处理：去掉行号（兼容 "1 内容"、"1. 内容"、"1、内容" 等编号格式，
        # 避免行号被金额正则误识别为金额）
        text = re.sub(r'^\s*\d+[.、)]?\s*', '', text, flags=re.MULTILINE)

        # 分段：检测是否同时包含抖加和垫付
        douyin_section = self._extract_section(text, '抖加')
        advance_section = self._extract_section(text, '垫付')

        all_details = []
        summary = {}

        if douyin_section and advance_section:
            # 混合文本：分别处理
            douyin_result = self._extract_single_section(douyin_section, "douyin_ad")
            advance_result = self._extract_single_section(advance_section, "advance_payment")
            
            all_details = douyin_result["details"] + advance_result["details"]
            
            summary = {
                "song_name": douyin_result["summary"].get("song_name", ""),
                "total_amount": douyin_result["summary"].get("total_amount", 0),
                "douyin_total": douyin_result["summary"].get("total_amount", 0),
                "advance_total": advance_result["summary"].get("total_amount", 0),
                "payer": douyin_result["summary"].get("payer", "") + " / " + advance_result["summary"].get("payer", ""),
                "apply_date": douyin_result["summary"].get("apply_date", ""),
                "expected_pay_date": advance_result["summary"].get("expected_pay_date", ""),
                "detail_count": len(all_details),
            }
            app_type = "mixed"
        elif douyin_section:
            result = self._extract_single_section(douyin_section, "douyin_ad")
            all_details = result["details"]
            summary = result["summary"]
            # 统一字段：单类型抖加时，把total_amount映射到douyin_total
            summary["douyin_total"] = summary.get("total_amount", 0)
            summary["advance_total"] = 0
            app_type = "douyin_ad"
        elif advance_section:
            result = self._extract_single_section(advance_section, "advance_payment")
            all_details = result["details"]
            summary = result["summary"]
            # 统一字段：单类型垫付时，把total_amount映射到advance_total
            summary["advance_total"] = summary.get("total_amount", 0)
            summary["douyin_total"] = 0
            app_type = "advance_payment"
        else:
            warnings.append("未识别到抖加或垫付申请")

        return {
            "app_type": app_type,
            "summary": summary,
            "details": [asdict(d) for d in all_details],
            "warnings": warnings,
            "raw_text": text,
        }

    def _extract_section(self, text: str, keyword: str) -> Optional[str]:
        """提取某一段落（抖加或垫付）"""
        # 找关键词的位置
        idx = text.find(keyword)
        if idx == -1:
            return None
        
        # 从关键词开始，到下一个关键词或文本结束
        next_idx = len(text)
        if keyword == '抖加' and '垫付' in text:
            next_idx = text.find('垫付')
        elif keyword == '垫付' and '抖加' in text:
            # 垫付在后面，直接到结尾
            pass
        
        return text[idx:next_idx]

    def _extract_single_section(self, text: str, app_type: str) -> dict:
        """提取单个部分的申请数据"""
        # 提取歌曲名
        song_matches = self.song_pattern.findall(text)
        song_name = song_matches[0] if song_matches else ""

        # 提取总金额
        total_amount = self._extract_total_amount(text)

        # 提取支付人
        payer_match = self.payer_pattern.search(text)
        payer = payer_match.group(1).strip() if payer_match else ""

        # 提取日期
        date_match = self.date_pattern.search(text)
        apply_date = date_match.group(1) if date_match else ""
        
        # 提取预计打款日期
        pay_date_match = re.search(r'预计打款日期[：:]\s*(\S+)', text)
        expected_pay_date = pay_date_match.group(1).strip() if pay_date_match else ""

        # 提取明细
        details = self._extract_details(text, app_type, song_name, payer, apply_date)

        return {
            "summary": {
                "song_name": song_name,
                "total_amount": total_amount,
                "payer": payer,
                "apply_date": apply_date,
                "expected_pay_date": expected_pay_date,
                "detail_count": len(details),
            },
            "details": details,
        }

    def _extract_total_amount(self, text: str) -> Optional[float]:
        """提取总金额"""
        patterns = [
            r'已支付[：:]\s*[¥￥]?(\d+(?:\.\d+)?)',
            r'已垫付[：:]\s*[¥￥]?(\d+(?:\.\d+)?)',
            r'总金额[：:]\s*[¥￥]?(\d+(?:\.\d+)?)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return float(match.group(1))
        return None

    def _extract_details(self, text: str, app_type: str,
                        song_name: str, payer: str, apply_date: str) -> List[Application]:
        """提取明细列表"""
        details = []
        lines = text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line or len(line) < 5:
                continue
            
            # 跳过汇总行
            if any(kw in line for kw in ['已支付', '已垫付', '统计', '共 ', '合计', '明细如下']):
                continue

            # 尝试提取一条明细
            detail = self._extract_single_detail(line, app_type, song_name, payer, apply_date)
            if detail:
                details.append(detail)
        
        return details

    def _extract_single_detail(self, line: str, app_type: str,
                               song_name: str, payer: str, apply_date: str) -> Optional[Application]:
        """从单行文本提取一条明细"""
        # 提取金额
        amount_match = self.amount_pattern.search(line)
        if not amount_match:
            return None
        
        amount = float(amount_match.group(1))
        if amount <= 0:
            return None

        # 提取博主名 - 多种模式
        blogger = ""
        
        # 模式1：博主名：xxx
        match = re.search(r'博主名[：:]\s*([^\s\-—（(]+)', line)
        if match:
            blogger = match.group(1).strip()
        
        # 模式2：【达人：xxx】
        if not blogger:
            match = re.search(r'【达人[：:]\s*([^】]+)】', line)
            if match:
                blogger = match.group(1).strip()
        
        # 模式3：为xxx垫付
        if not blogger:
            match = re.search(r'为\s*([^\s，。、]+?)\s*垫付', line)
            if match:
                blogger = match.group(1).strip()
        
        # 模式4：用-分隔的第二段
        if not blogger and '-' in line:
            parts = line.split('-')
            if len(parts) >= 2:
                blogger = parts[1].strip()
                # 去掉金额部分
                blogger = re.sub(r'[¥￥]?\d+.*$', '', blogger).strip()

        # 清理前缀
        blogger = re.sub(r'^(博主名|达人|博主)[：:]\s*', '', blogger).strip()
        # 清理后缀
        blogger = re.sub(r'】?\s*垫付.*$', '', blogger).strip()
        blogger = re.sub(r'】?\s*申请.*$', '', blogger).strip()

        if not blogger or len(blogger) < 1:
            return None

        return Application(
            app_type=app_type,
            song_name=song_name,
            blogger_name=blogger,
            amount=amount,
            payer=payer,
            apply_date=apply_date,
            raw_text=line,
            confidence=1.0,  # 规则提取置信度=1.0
        )

    def extract_by_llm(self, text: str) -> List[Application]:
        """
        用大模型提取结构化数据
        规则提取失败时的兜底方案

        实际接入时，在这里调用LLM API（豆包/GPT/Gemini Flash）
        :param text: 原始文本
        :return: 提取的明细列表
        """
        if not self.use_llm or not self.llm_client:
            return []

        # 构造Prompt
        prompt = self.LLM_EXTRACT_PROMPT.format(text=text)

        # 最多尝试 2 次：模型偶发输出格式错误时，重试一次可显著提升成功率
        last_error = ""
        for attempt in range(2):
            try:
                # 调用LLM
                response = self.llm_client.chat(prompt)

                # 清理返回，提取JSON部分
                # 有些模型会在JSON前后加```json标记，或包一层对象 {"明细": [...]}
                json_match = re.search(r'\[.*\]', response, re.DOTALL)
                raw = json_match.group(0) if json_match else response

                # 解析LLM返回的JSON
                llm_results = json.loads(raw)
                if not isinstance(llm_results, list):
                    # 兼容：对象里嵌数组（如 {"报销申报明细": [...]}），取第一个数组值
                    for v in llm_results.values():
                        if isinstance(v, list):
                            llm_results = v
                            break
                if not isinstance(llm_results, list):
                    raise ValueError("LLM 返回内容不是 JSON 数组")

                # 字段校验 + 转成 Application
                details = self._build_details(llm_results, text)
                if not details:
                    raise ValueError("LLM 返回明细为空或字段不合法")
                return details
            except Exception as e:
                last_error = str(e)
                print(f"[Extractor] LLM 第{attempt+1}次提取失败: {last_error}")

        print(f"[Extractor] LLM 重试后仍失败，放弃提取: {last_error}")
        return []

    @staticmethod
    def _build_details(llm_results: list, text: str) -> List[Application]:
        """
        把 LLM 返回的 JSON 数组转成 Application 列表，带字段合法性校验。
        兼容模型字段名漂移（如"相关方/达人/博主"→blogger_name、"资金性质/用途"→app_type 推断）。
        非法条目（类型枚举错误 / 金额非正数 / 博主名为空）直接丢弃，避免脏数据进入下游。
        """
        VALID_APP_TYPES = {"douyin_ad", "advance_payment"}
        # 字段名容错映射：模型可能自由发挥的字段名 → 系统字段名
        FIELD_ALIASES = {
            "app_type": ("app_type", "申请类型", "类型"),
            "blogger_name": ("blogger_name", "博主名", "博主", "达人名", "达人", "相关方", "账号", "抖音昵称", "昵称"),
            "amount": ("amount", "金额", "费用", "价格", "款额"),
            "payer": ("payer", "支付人", "打款人", "付款人", "支付方", "付款方"),
            "apply_date": ("apply_date", "申请日期", "日期", "申报日期"),
            "song_name": ("song_name", "歌名", "歌曲", "歌曲名"),
        }
        # app_type 推断关键词：从"资金性质/用途/类型"等字段判断
        TYPE_HINTS = {
            "advance_payment": ("垫付", "预付", "代付"),
            "douyin_ad": ("抖加", "dou+", "DOU+", "推广充值", "买量"),
        }

        def _pick(item: dict, system_field: str):
            for alias in FIELD_ALIASES.get(system_field, ()):
                if alias in item and item[alias] is not None:
                    return item[alias]
            return ""

        def _infer_app_type(item: dict) -> str:
            app_type = str(_pick(item, "app_type")).strip().lower()
            if app_type in VALID_APP_TYPES:
                return app_type
            # 从自由字段中推断（资金性质/用途/类型 等）
            for key, val in item.items():
                if isinstance(val, str):
                    low = val.lower()
                    if any(kw in low for kw in TYPE_HINTS["advance_payment"]):
                        return "advance_payment"
                    if any(kw in low for kw in TYPE_HINTS["douyin_ad"]):
                        return "douyin_ad"
            # 从原文兜底判断
            if "垫付" in text:
                return "advance_payment"
            if "抖加" in text or "dou+" in text.lower():
                return "douyin_ad"
            return ""

        details = []
        for item in llm_results:
            if not isinstance(item, dict):
                continue
            app_type = _infer_app_type(item)
            if app_type not in VALID_APP_TYPES:
                continue
            amount_raw = _pick(item, "amount")
            try:
                # 兼容 "350"、"350元"、"¥350"、"350块" 等写法
                amount = float(str(amount_raw).replace("¥", "").replace("￥", "")
                               .replace("元", "").replace("块", "").strip())
            except (TypeError, ValueError):
                continue
            blogger_name = str(_pick(item, "blogger_name")).strip()
            if amount <= 0 or not blogger_name:
                continue
            details.append(Application(
                app_type=app_type,
                song_name=str(_pick(item, "song_name")).strip(),
                blogger_name=blogger_name,
                amount=amount,
                payer=str(_pick(item, "payer")).strip(),
                apply_date=str(_pick(item, "apply_date")).strip(),
                raw_text=text,
                confidence=0.7,  # LLM提取置信度=0.7，提醒人工核对
            ))
        return details

    def extract_with_fallback(self, text: str, app_type: str = "auto") -> dict:
        """
        分层提取：先用规则，规则失败再用LLM
        这是实际生产环境的推荐方案（master 主流程调用入口）
        """
        # 第一层：规则提取
        result = self.extract(text, app_type)

        # 如果规则提取到的明细太少，可能格式不对
        if len(result["details"]) < 2 and self.use_llm:
            # 第二层：LLM提取兜底
            llm_details = self.extract_by_llm(text)
            if llm_details:
                # 用LLM的结果替换规则结果
                result["details"] = [asdict(d) for d in llm_details]
                result["warnings"].append("使用LLM提取，置信度较低，请人工核对")

        return result
