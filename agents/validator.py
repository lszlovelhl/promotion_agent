"""
数据校验 Agent
校验申请数据的一致性和完整性
"""
from typing import List, Dict, Tuple
from dataclasses import dataclass


@dataclass
class ValidationResult:
    """校验结果"""
    is_valid: bool  # 是否通过
    errors: List[dict]  # 错误（必须人工处理）
    warnings: List[dict]  # 警告（建议人工确认）
    info: List[str]  # 信息


class ValidatorAgent:
    """数据校验 Agent"""
    
    def __init__(self):
        self.existing_bloggers = []  # 表格里已有的博主名
        self.existing_songs = []  # 表格里已有的歌名
    
    def set_existing_data(self, bloggers: list, songs: list):
        """设置表格里已有的博主和歌名，用于模糊匹配笔误"""
        self.existing_bloggers = bloggers
        self.existing_songs = songs
    
    def _edit_distance(self, s1: str, s2: str) -> int:
        """计算两个字符串的编辑距离"""
        m, n = len(s1), len(s2)
        dp = [[0]*(n+1) for _ in range(m+1)]
        for i in range(m+1):
            dp[i][0] = i
        for j in range(n+1):
            dp[0][j] = j
        for i in range(1, m+1):
            for j in range(1, n+1):
                if s1[i-1] == s2[j-1]:
                    dp[i][j] = dp[i-1][j-1]
                else:
                    dp[i][j] = 1 + min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1])
        return dp[m][n]
    
    def _check_typo(self, name: str, existing_list: list, type_name: str, warnings: list):
        """检查是否疑似笔误"""
        if not name or not existing_list:
            return
        # 精确匹配到了就不用管
        if name in existing_list:
            return
        # 模糊匹配：编辑距离小于等于2，且长度大于3
        for existing in existing_list:
            dist = self._edit_distance(name, existing)
            max_len = max(len(name), len(existing))
            # 短名（<=2字）同音/形近笔误很常见（如 初秋→初湫），编辑距离 1 即视为疑似；
            # 长名用相似度 70% 阈值，避免误报
            if max_len <= 2:
                suspect = dist <= 1
            elif max_len >= 3 and dist <= 2:
                similarity = 1 - dist / max_len
                suspect = similarity >= 0.7
            else:
                suspect = False
            if suspect:
                similarity = 1 - dist / max_len
                warnings.append({
                    "field": "typo",
                    "message": f"疑似笔误：您写的「{name}」，{type_name}列表里有「{existing}」，相似度{int(similarity*100)}%，是否是同一个？",
                    "level": "warning"
                })
                break
    
    def validate(self, extracted_data: dict) -> ValidationResult:
        """
        校验提取的数据
        """
        errors = []
        warnings = []
        info = []

        summary = extracted_data.get("summary", {})
        details = extracted_data.get("details", [])
        app_type = extracted_data.get("app_type", "")
        raw_text = extracted_data.get("raw_text", "")

        # 1. 字段完整性校验
        self._check_required_fields(summary, details, errors, warnings)

        # 2. 金额一致性校验：明细之和 vs 总金额
        self._check_amount_consistency(summary, details, errors, warnings)

        # 3. 重复申请校验
        self._check_duplicates(details, warnings)

        # 4. 日期合理性校验
        self._check_date_reasonableness(summary, warnings)
        
        # 4.1 日期逻辑校验：打款日期是否早于申请日期
        self.check_date_logic(
            summary.get("apply_date", ""),
            summary.get("expected_pay_date", ""),
            errors, warnings
        )

        # 5. 明细数量校验
        self._check_detail_count(summary, details, warnings)

        # 6. 金额合理性校验
        self._check_amount_reasonableness(details, warnings)

        # 7. 笔数一致性校验（新增）
        self.check_count_consistency(raw_text, details, app_type, warnings)
        
        # 8. 笔误检查：博主名和歌名
        for d in details:
            self._check_typo(d.get("blogger_name", ""), self.existing_bloggers, "博主", warnings)
        self._check_typo(summary.get("song_name", ""), self.existing_songs, "歌曲", warnings)

        is_valid = len(errors) == 0

        return ValidationResult(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            info=info,
        )

    def _check_required_fields(self, summary: dict, details: list,
                                errors: list, warnings: list):
        """检查必填字段"""
        if not summary.get("song_name"):
            errors.append({
                "field": "song_name",
                "message": "缺少歌曲名",
                "level": "error"
            })

        if summary.get("total_amount") is None:
            errors.append({
                "field": "total_amount",
                "message": "缺少总金额",
                "level": "error"
            })

        if not summary.get("payer"):
            warnings.append({
                "field": "payer",
                "message": "缺少支付人，请确认",
                "level": "warning"
            })

        if not summary.get("apply_date"):
            warnings.append({
                "field": "apply_date",
                "message": "缺少申请日期，请确认",
                "level": "warning"
            })

        if not details:
            errors.append({
                "field": "details",
                "message": "未提取到任何明细，请手动添加",
                "level": "error"
            })

    def _check_amount_consistency(self, summary: dict, details: list,
                                   errors: list, warnings: list):
        """校验金额一致性"""
        # 如果是混合类型，分别校验抖加和垫付
        if "douyin_total" in summary and "advance_total" in summary:
            # 抖加校验
            douyin_details = [d for d in details if d.get("app_type") == "douyin_ad"]
            douyin_sum = sum(d.get("amount", 0) for d in douyin_details)
            douyin_expected = summary.get("douyin_total", 0)
            if abs(douyin_sum - douyin_expected) > 0.01:
                errors.append({
                    "field": "douyin_amount",
                    "message": f"抖加金额不一致：明细合计 ¥{douyin_sum} vs 总金额 ¥{douyin_expected}，差额 ¥{douyin_sum - douyin_expected:.2f}",
                    "expected": douyin_expected,
                    "actual": douyin_sum,
                    "diff": douyin_sum - douyin_expected,
                    "level": "error"
                })

            # 垫付校验
            advance_details = [d for d in details if d.get("app_type") == "advance_payment"]
            advance_sum = sum(d.get("amount", 0) for d in advance_details)
            advance_expected = summary.get("advance_total", 0)
            if abs(advance_sum - advance_expected) > 0.01:
                errors.append({
                    "field": "advance_amount",
                    "message": f"垫付金额不一致：明细合计 ¥{advance_sum} vs 总金额 ¥{advance_expected}，差额 ¥{advance_sum - advance_expected:.2f}",
                    "expected": advance_expected,
                    "actual": advance_sum,
                    "diff": advance_sum - advance_expected,
                    "level": "error"
                })
            return

        # 单类型校验
        total_amount = summary.get("total_amount")
        if total_amount is None or not details:
            return

        detail_sum = sum(d.get("amount", 0) for d in details)
        diff = total_amount - detail_sum

        if abs(diff) > 0.01:  # 允许0.01的误差
            errors.append({
                "field": "amount",
                "message": f"金额不一致：明细合计 {detail_sum} 元 vs 总金额 {total_amount} 元，差额 {diff:.2f} 元",
                "expected": total_amount,
                "actual": detail_sum,
                "diff": diff,
                "level": "error"
            })

    def _check_duplicates(self, details: list, warnings: list):
        """检查重复申请"""
        seen = {}
        for i, d in enumerate(details):
            key = (d.get("blogger_name", "").strip(), 
                   d.get("amount", 0),
                   d.get("song_name", "").strip())
            if key in seen:
                warnings.append({
                    "field": "duplicate",
                    "message": f"疑似重复申请：博主「{d.get('blogger_name')}」金额 {d.get('amount')} 元",
                    "first_index": seen[key],
                    "current_index": i,
                    "level": "warning"
                })
            else:
                seen[key] = i

    def _check_date_reasonableness(self, summary: dict, warnings: list):
        """检查日期合理性"""
        date_str = summary.get("apply_date", "")
        if not date_str:
            return

        # 简单检查：日期格式是否合理
        import datetime
        parsed_date = None
        try:
            # 尝试解析常见格式
            for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y', '%d/%m/%y', '%Y年%m月%d日']:
                try:
                    parsed_date = datetime.datetime.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    continue
        except Exception:
            pass

        if parsed_date is None:
            warnings.append({
                "field": "apply_date",
                "message": f"日期格式不标准：{date_str}，可能存在歧义，请确认",
                "level": "warning"
            })
            return

        today = datetime.date.today()
        # 检查是否是未来日期
        if parsed_date > today + datetime.timedelta(days=7):
            warnings.append({
                "field": "apply_date",
                "message": f"日期 {date_str} 是未来日期，请确认",
                "level": "warning"
            })
        # 检查是否太旧
        if parsed_date < today - datetime.timedelta(days=365):
            warnings.append({
                "field": "apply_date",
                "message": f"日期 {date_str} 超过一年前，请确认",
                "level": "warning"
            })

    def _check_detail_count(self, summary: dict, details: list, warnings: list):
        """检查明细数量与声称的笔数是否一致"""
        # 这个需要从原始文本中提取声称的笔数，暂时跳过
        # 留给后续优化
        pass

    def _check_amount_reasonableness(self, details: list, warnings: list):
        """检查金额合理性"""
        # 抖加常见档位
        common_douyin_amounts = {100, 200, 300, 500, 1000, 2000, 5000}
        seen_blogger_amounts = {}
        
        for i, d in enumerate(details):
            amount = d.get("amount", 0)
            blogger = d.get("blogger_name", "")
            app_type = d.get("app_type", "")
            
            # 1. 金额为0或负数
            if amount <= 0:
                warnings.append({
                    "field": "amount",
                    "message": f"第{i+1}条明细金额 {amount} 元异常，应为正数",
                    "level": "warning"
                })
                continue
            
            # 2. 单条金额过大
            if amount > 10000:
                warnings.append({
                    "field": "amount",
                    "message": f"第{i+1}条明细「{blogger}」金额 {amount} 元较大，请确认",
                    "level": "warning"
                })
            
            # 3. 抖加金额档位校验
            if app_type == "douyin_ad" and amount not in common_douyin_amounts:
                warnings.append({
                    "field": "amount",
                    "message": f"第{i+1}条「{blogger}」抖加金额 {amount} 元不是常见档位（100/200/300/500/1000），请确认",
                    "level": "warning"
                })
            
            # 4. 同一博主重复金额校验
            key = (blogger, amount)
            if key in seen_blogger_amounts:
                warnings.append({
                    "field": "amount",
                    "message": f"博主「{blogger}」申请金额 {amount} 元与之前的记录重复，请确认是否重复申请",
                    "level": "warning"
                })
            else:
                seen_blogger_amounts[key] = i

    def check_count_consistency(self, text: str, details: list, app_type: str, warnings: list):
        """
        检查声称的笔数与实际明细数是否一致
        题目里的坑：抖加7笔是对的（初秋分3笔+艳红分2笔），但垫付说7笔只列了5个明细
        """
        import re

        # 混合文本：分别处理抖加和垫付段
        if app_type == "mixed":
            # 找抖加段
            douyin_idx = text.find("抖加")
            advance_idx = text.find("垫付")

            if douyin_idx != -1 and advance_idx != -1:
                # 抖加段
                douyin_section = text[douyin_idx:advance_idx]
                douyin_details = [d for d in details if d.get("app_type") == "douyin_ad"]
                self._check_count_in_section(douyin_section, douyin_details, "抖加", warnings)

                # 垫付段
                advance_section = text[advance_idx:]
                advance_details = [d for d in details if d.get("app_type") == "advance_payment"]
                self._check_count_in_section(advance_section, advance_details, "垫付", warnings)
            return

        # 单类型
        self._check_count_in_section(text, details, app_type, warnings)

    def _check_count_in_section(self, section_text: str, section_details: list,
                                 section_name: str, warnings: list):
        """检查某一段落的笔数一致性"""
        import re
        # 找"共X笔"的模式
        count_match = re.search(r'共\s*(\d+)\s*笔', section_text)
        if not count_match:
            return

        claimed_count = int(count_match.group(1))
        actual_count = len(section_details)

        # 抖加：需要考虑分笔情况（"分3笔"、"分2笔"）
        if section_name == "抖加" or section_name == "douyin_ad":
            # 统计分笔数
            extra = 0
            for d in section_details:
                raw = d.get("raw_text", "")
                split_match = re.search(r'分\s*(\d+)\s*笔', raw)
                if split_match:
                    extra += int(split_match.group(1)) - 1  # 减去当前这1条
            actual_total = actual_count + extra
            if actual_total != claimed_count:
                warnings.append({
                    "field": "count",
                    "message": f"{section_name}笔数不一致：声称共 {claimed_count} 笔，按分笔计算实际为 {actual_total} 笔",
                    "level": "warning"
                })
        else:
            # 垫付：没有分笔说明，直接对比
            if actual_count != claimed_count:
                warnings.append({
                    "field": "count",
                    "message": f"{section_name}笔数不一致：声称共 {claimed_count} 笔申请，但明细只列了 {actual_count} 条，是否有遗漏？",
                    "level": "warning"
                })

    def check_date_logic(self, apply_date: str, pay_date: str, errors: list, warnings: list):
        """
        检查日期逻辑：打款日期是否在申请日期之后
        """
        if not apply_date or not pay_date:
            return

        import datetime
        # 解析申请日期
        apply_dt = None
        for fmt in ['%Y-%m-%d', '%Y/%m/%d']:
            try:
                apply_dt = datetime.datetime.strptime(apply_date, fmt).date()
                break
            except:
                pass

        # 解析打款日期（可能是各种格式）
        pay_dt = None
        for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%y/%m/%d', '%d/%m/%y', '%m/%d/%y']:
            try:
                pay_dt = datetime.datetime.strptime(pay_date, fmt).date()
                break
            except:
                pass

        if apply_dt and pay_dt:
            if pay_dt < apply_dt:
                warnings.append({
                    "field": "pay_date",
                    "message": f"打款日期（{pay_date}）早于申请日期（{apply_date}），逻辑异常，请确认",
                    "level": "warning"
                })
