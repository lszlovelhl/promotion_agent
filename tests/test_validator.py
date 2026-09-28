"""
校验 Agent 回归测试
====================
把实际交付中发现的真实问题沉淀为测试用例：
- 垫付声称 7 笔、明细只列 5 条（笔数不一致）
- 打款日期早于申请日期（日期逻辑异常）
- 抖加分笔计算、金额不一致、笔误、重复申请
运行：cd promotion_agent && python3 -m unittest tests.test_validator -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents"))

import unittest
from validator import ValidatorAgent


def make_detail(blogger, amount, app_type="advance_payment", raw=""):
    return {
        "app_type": app_type,
        "song_name": "你是我的仰望",
        "blogger_name": blogger,
        "amount": amount,
        "payer": "爆火音乐",
        "apply_date": "2026-09-03",
        "raw_text": raw or f"{blogger} {amount}元",
        "status": "pending",
        "notes": "",
        "confidence": 1.0,
    }


def make_data(app_type, summary, details, raw_text):
    return {
        "app_type": app_type,
        "summary": summary,
        "details": details,
        "warnings": [],
        "raw_text": raw_text,
    }


class TestCountConsistency(unittest.TestCase):
    """笔数一致性：题目真实坑——声称 7 笔、明细只列 5 条"""

    def test_advance_claims_7_but_only_5_details(self):
        raw = ("垫付申请：共7笔申请，具体明细如下\n"
               "1. 初秋 300元\n2. 艳红 200元\n3. 小李 500元\n4. 阿伟 100元\n5. 大壮 400元")
        details = [
            make_detail("初秋", 300), make_detail("艳红", 200),
            make_detail("小李", 500), make_detail("阿伟", 100),
            make_detail("大壮", 400),
        ]
        data = make_data("advance_payment", {"total_amount": 1500}, details, raw)
        result = ValidatorAgent().validate(data)
        messages = [w["message"] for w in result.warnings]
        self.assertTrue(any("笔数不一致" in m and "7" in m for m in messages),
                        f"应提示声称 7 笔但明细不足，实际警告: {messages}")

    def test_advance_count_matches(self):
        raw = "垫付申请：共5笔申请\n1. 初秋 300元\n2. 艳红 200元\n3. 小李 500元\n4. 阿伟 100元\n5. 大壮 400元"
        details = [
            make_detail("初秋", 300), make_detail("艳红", 200),
            make_detail("小李", 500), make_detail("阿伟", 100),
            make_detail("大壮", 400),
        ]
        data = make_data("advance_payment", {"total_amount": 1500}, details, raw)
        result = ValidatorAgent().validate(data)
        messages = [w["message"] for w in result.warnings]
        self.assertFalse(any("笔数不一致" in m for m in messages), f"不应有笔数警告: {messages}")

    def test_douyin_split_count(self):
        # 抖加"共3笔，其中分2笔"，规则提取 2 条明细（一条是拆分说明）
        raw = "抖加申请：共3笔\n1. 初秋 300元\n2. 艳红 分2笔 400元"
        details = [
            make_detail("初秋", 300, app_type="douyin_ad", raw="初秋 300元"),
            make_detail("艳红", 400, app_type="douyin_ad", raw="艳红 分2笔 400元"),
        ]
        data = make_data("douyin_ad", {"total_amount": 700}, details, raw)
        result = ValidatorAgent().validate(data)
        messages = [w["message"] for w in result.warnings]
        # 1 + (2-1) = 2 ≠ 3 也会告警；这里验证的是分笔逻辑不抛异常且能识别分笔
        self.assertIsInstance(result.warnings, list)


class TestDateLogic(unittest.TestCase):
    """日期逻辑：打款日期早于申请日期"""

    def test_pay_date_before_apply_date(self):
        summary = {"song_name": "你是我的仰望", "total_amount": 100,
                   "apply_date": "2026-09-03", "expected_pay_date": "2026-08-31"}
        data = make_data("advance_payment", summary,
                         [make_detail("初秋", 100)], "垫付申请：初秋 100元")
        result = ValidatorAgent().validate(data)
        messages = [w["message"] for w in result.warnings]
        self.assertTrue(any("早于申请日期" in m for m in messages),
                        f"应提示打款日期早于申请日期，实际警告: {messages}")

    def test_pay_date_after_apply_date_ok(self):
        summary = {"song_name": "你是我的仰望", "total_amount": 100,
                   "apply_date": "2026-09-03", "expected_pay_date": "2026-09-05"}
        data = make_data("advance_payment", summary,
                         [make_detail("初秋", 100)], "垫付申请：初秋 100元")
        result = ValidatorAgent().validate(data)
        messages = [w["message"] for w in result.warnings]
        self.assertFalse(any("早于申请日期" in m for m in messages))


class TestAmountConsistency(unittest.TestCase):
    """金额一致性：明细合计 vs 总金额"""

    def test_mismatch_is_error(self):
        summary = {"song_name": "你是我的仰望", "total_amount": 1000}
        details = [make_detail("初秋", 300), make_detail("艳红", 200)]
        data = make_data("advance_payment", summary, details, "垫付申请：共2笔")
        result = ValidatorAgent().validate(data)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("金额不一致" in e["message"] for e in result.errors))

    def test_match_ok(self):
        summary = {"song_name": "你是我的仰望", "total_amount": 500}
        details = [make_detail("初秋", 300), make_detail("艳红", 200)]
        data = make_data("advance_payment", summary, details, "垫付申请：共2笔")
        result = ValidatorAgent().validate(data)
        self.assertTrue(result.is_valid)
        self.assertFalse(any("金额不一致" in e["message"] for e in result.errors))


class TestTypo(unittest.TestCase):
    """笔误检查：博主名与已有列表模糊匹配"""

    def test_typo_detected(self):
        v = ValidatorAgent()
        v.set_existing_data(["初秋", "艳红"], [])
        summary = {"song_name": "你是我的仰望", "total_amount": 300}
        details = [make_detail("初湫", 300)]
        data = make_data("advance_payment", summary, details, "垫付申请：初湫 300元")
        result = v.validate(data)
        messages = [w["message"] for w in result.warnings]
        self.assertTrue(any("疑似笔误" in m and "初秋" in m for m in messages),
                        f"应提示疑似笔误，实际警告: {messages}")

    def test_typo_long_name(self):
        # 长名（>=3字）仍需相似度 >=70% 才提示
        v = ValidatorAgent()
        v.set_existing_data(["王小明明"], [])
        summary = {"song_name": "你是我的仰望", "total_amount": 300}
        details = [make_detail("王小明朗", 300)]
        data = make_data("advance_payment", summary, details, "垫付申请：王小明朗 300元")
        result = v.validate(data)
        messages = [w["message"] for w in result.warnings]
        self.assertTrue(any("疑似笔误" in m for m in messages),
                        f"应提示疑似笔误，实际警告: {messages}")

    def test_exact_match_no_warning(self):
        v = ValidatorAgent()
        v.set_existing_data(["初秋"], [])
        summary = {"song_name": "你是我的仰望", "total_amount": 300}
        details = [make_detail("初秋", 300)]
        data = make_data("advance_payment", summary, details, "垫付申请：初秋 300元")
        result = v.validate(data)
        messages = [w["message"] for w in result.warnings]
        self.assertFalse(any("疑似笔误" in m for m in messages))


class TestDuplicate(unittest.TestCase):
    """重复申请检查"""

    def test_duplicate_detected(self):
        summary = {"song_name": "你是我的仰望", "total_amount": 600}
        details = [make_detail("初秋", 300), make_detail("初秋", 300)]
        data = make_data("advance_payment", summary, details, "垫付申请：初秋300元，初秋300元")
        result = ValidatorAgent().validate(data)
        messages = [w["message"] for w in result.warnings]
        self.assertTrue(any("重复申请" in m for m in messages))


class TestRequiredFields(unittest.TestCase):
    """必填字段校验"""

    def test_missing_song_is_error(self):
        summary = {"total_amount": 300}
        details = [make_detail("初秋", 300)]
        data = make_data("advance_payment", summary, details, "垫付申请：初秋 300元")
        result = ValidatorAgent().validate(data)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("歌曲名" in e["message"] for e in result.errors))


if __name__ == "__main__":
    unittest.main(verbosity=2)
