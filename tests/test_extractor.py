"""
提取 Agent 回归测试
====================
覆盖：规则提取（抖加 / 垫付 / 混合文本）、LLM 返回字段校验。
运行：cd promotion_agent && python3 -m unittest tests.test_extractor -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents"))

import unittest
from extractor import ExtractorAgent


class TestRuleExtraction(unittest.TestCase):
    """规则提取：抖加 / 垫付 / 混合文本"""

    def test_douyin_ad(self):
        text = ("【抖加申请】歌曲《你是我的仰望》\n"
                "已支付：500元\n"
                "1. 博主名：初秋 300元\n"
                "2. 博主名：艳红 200元")
        result = ExtractorAgent().extract(text)
        self.assertEqual(result["app_type"], "douyin_ad")
        self.assertEqual(result["summary"]["song_name"], "你是我的仰望")
        self.assertEqual(result["summary"]["total_amount"], 500)
        self.assertEqual(len(result["details"]), 2)
        self.assertEqual(result["details"][0]["blogger_name"], "初秋")
        self.assertEqual(result["details"][0]["amount"], 300)

    def test_advance_payment(self):
        text = ("【垫付申请】歌曲《海阔天空》\n"
                "已垫付：800元\n"
                "1. 博主名：阿伟 500元\n"
                "2. 博主名：大壮 300元")
        result = ExtractorAgent().extract(text)
        self.assertEqual(result["app_type"], "advance_payment")
        self.assertEqual(result["summary"]["song_name"], "海阔天空")
        self.assertEqual(len(result["details"]), 2)

    def test_mixed_text(self):
        text = ("【抖加申请】歌曲《你是我的仰望》 已支付：500元\n"
                "1. 博主名：初秋 300元\n2. 博主名：艳红 200元\n"
                "【垫付申请】歌曲《海阔天空》 已垫付：800元\n"
                "1. 博主名：阿伟 500元\n2. 博主名：大壮 300元")
        result = ExtractorAgent().extract(text)
        self.assertEqual(result["app_type"], "mixed")
        self.assertEqual(len(result["details"]), 4)
        self.assertEqual(result["summary"]["douyin_total"], 500)
        self.assertEqual(result["summary"]["advance_total"], 800)

    def test_unknown_text(self):
        result = ExtractorAgent().extract("今天天气不错")
        self.assertEqual(len(result["details"]), 0)
        self.assertTrue(len(result["warnings"]) > 0)


class TestLLMJsonValidation(unittest.TestCase):
    """LLM 返回的字段校验：非法条目应被丢弃"""

    def test_build_details_filters_invalid(self):
        llm_results = [
            {"app_type": "douyin_ad", "song_name": "你是我的仰望",
             "blogger_name": "初秋", "amount": 300, "payer": "爆火音乐", "apply_date": "2026-09-03"},
            {"app_type": "invalid_type", "song_name": "x", "blogger_name": "张三", "amount": 100},  # 类型非法
            {"app_type": "advance_payment", "song_name": "x", "blogger_name": "李四", "amount": -5},  # 金额非正
            {"app_type": "advance_payment", "song_name": "x", "blogger_name": "", "amount": 100},    # 博主为空
            {"app_type": "advance_payment", "song_name": "x", "blogger_name": "王五", "amount": "abc"},  # 金额非数字
            "not-a-dict",  # 非对象
        ]
        details = ExtractorAgent._build_details(llm_results, "raw text")
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0].blogger_name, "初秋")
        self.assertEqual(details[0].confidence, 0.7)

    def test_build_details_amount_string_converts(self):
        llm_results = [
            {"app_type": "advance_payment", "song_name": "x",
             "blogger_name": "初秋", "amount": "300.5", "payer": "", "apply_date": ""},
        ]
        details = ExtractorAgent._build_details(llm_results, "raw")
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0].amount, 300.5)

    def test_build_details_field_alias_mapping(self):
        """字段名容错：LLM 自由发挥的字段（相关方/资金性质/金额带单位）也能转成系统字段"""
        llm_results = [
            {"相关方": "小美博主", "金额": "350块", "资金性质/用途": "垫付购买推广费用"},
            {"相关方": "大强哥", "金额": 200, "资金性质/用途": "充值抖加费用"},
        ]
        details = ExtractorAgent._build_details(llm_results, "麻烦处理下：小美垫付350块，大强充抖加200")
        self.assertEqual(len(details), 2)
        by_name = {d.blogger_name: d for d in details}
        self.assertEqual(by_name["小美博主"].app_type, "advance_payment")
        self.assertEqual(by_name["小美博主"].amount, 350.0)
        self.assertEqual(by_name["大强哥"].app_type, "douyin_ad")
        self.assertEqual(by_name["大强哥"].amount, 200.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
