#!/usr/bin/env python3
"""
提取准确率评测
==============
基于 data/records.json 的真实人工确认记录构造评测集（文本按系统支持的真实句式生成，
博主/金额/类型/日期均来自已确认记录），评估提取 Agent 的：
- 条目级 精确率 / 召回率 / F1
- 字段级 博主名 / 类型 / 金额 正确率

默认使用规则提取（免费、离线）；加 --with-llm 时用火山方舟 LLM 兜底模式对比。

用法：
    python3 tools/eval_extractor.py [--with-llm]
"""
import json
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.extractor import ExtractorAgent
from llm_client import LLMClient

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
REPORT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_report.md")


def load_ground_truth() -> list:
    """从 records.json 加载人工确认的真值（博主/类型/金额）"""
    with open(os.path.join(DATA_DIR, "records.json"), "r", encoding="utf-8") as f:
        records = json.load(f)
    return [
        {
            "blogger_name": r.get("blogger_name", ""),
            "app_type": r.get("app_type", ""),
            "amount": float(r.get("amount", 0)),
        }
        for r in records
    ]


def build_eval_texts(truth: list) -> list:
    """
    按系统支持的真实句式构造评测输入。
    返回 [{text, expected:[{blogger_name, app_type, amount}]}]
    """
    douyin = [r for r in truth if r["app_type"] == "douyin_ad"]
    advance = [r for r in truth if r["app_type"] == "advance_payment"]

    texts = []
    if douyin:
        lines = ["抖加申请"]
        for r in douyin:
            lines.append(f"博主名：{r['blogger_name']} {int(r['amount'])}元")
        lines.append("由爆火音乐支付")
        texts.append({"text": "\n".join(lines), "expected": douyin})

    if advance:
        lines = ["垫付申请"]
        for r in advance:
            lines.append(f"为{r['blogger_name']}垫付 {int(r['amount'])}元")
        lines.append("由林老师支付")
        texts.append({"text": "\n".join(lines), "expected": advance})

    return texts


def _norm_amount(v) -> float:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return -1.0


def evaluate(text: str, expected: list, extractor) -> dict:
    """对一段文本做提取评估"""
    result = extractor.extract(text, "auto")
    details = result.get("details", [])
    extracted = [
        {"blogger_name": d.get("blogger_name", ""),
         "app_type": d.get("app_type", ""),
         "amount": _norm_amount(d.get("amount"))}
        for d in details
    ]

    # 匹配：条目级（博主+类型+金额全对）
    exp_keys = [(r["blogger_name"], r["app_type"], r["amount"]) for r in expected]
    ext_keys = [(r["blogger_name"], r["app_type"], r["amount"]) for r in extracted]
    tp = len(set(exp_keys) & set(ext_keys))
    precision = tp / len(ext_keys) if ext_keys else 0.0
    recall = tp / len(exp_keys) if exp_keys else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    # 字段级
    field_ok = {"blogger_name": 0, "app_type": 0, "amount": 0}
    for e in expected:
        for d in extracted:
            if d["blogger_name"] == e["blogger_name"] and d["app_type"] == e["app_type"]:
                if d["amount"] == e["amount"]:
                    field_ok["amount"] += 1
                field_ok["blogger_name"] += 1
                field_ok["app_type"] += 1
                break
    n = len(expected)
    field_acc = {k: (v / n if n else 1.0) for k, v in field_ok.items()}

    return {
        "expected_count": len(expected),
        "extracted_count": len(extracted),
        "matched_count": tp,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "field_accuracy": field_acc,
    }


def main():
    parser = argparse.ArgumentParser(description="提取准确率评测")
    parser.add_argument("--with-llm", action="store_true", help="同时评测 LLM 兜底模式")
    args = parser.parse_args()

    truth = load_ground_truth()
    cases = build_eval_texts(truth)
    print(f"评测集：{len(cases)} 段文本，真值 {len(truth)} 条记录（来自 records.json 人工确认记录）\n")

    llm_client = None
    if args.with_llm:
        try:
            with open(os.path.join(DATA_DIR, "config.json"), "r", encoding="utf-8") as f:
                cfg = json.load(f)
            llm_client = LLMClient()
            llm_client.set_config(cfg.get("llm_api_key", ""), cfg.get("llm_model", ""))
            print("[LLM 模式] 已加载火山方舟配置")
        except Exception as e:
            print(f"[LLM 模式] 配置加载失败，跳过: {e}")

    report_lines = [
        "# 提取准确率评测报告",
        "",
        f"- 生成时间：{__import__('time').strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 数据来源：data/records.json（人工确认记录 {len(truth)} 条）",
        f"- 评测文本：按系统支持的真实句式构造（博主/金额/类型来自真实记录）",
        "",
    ]

    modes = {"规则提取": ExtractorAgent(use_llm=False)}
    if llm_client:
        modes["规则+LLM兜底"] = ExtractorAgent(use_llm=True, llm_client=llm_client)

    for mode_name, extractor in modes.items():
        agg = {"expected": 0, "extracted": 0, "matched": 0, "bb": 0, "bt": 0, "ba": 0}
        print(f"===== {mode_name} =====")
        for case in cases:
            r = evaluate(case["text"], case["expected"], extractor)
            agg["expected"] += r["expected_count"]
            agg["extracted"] += r["extracted_count"]
            agg["matched"] += r["matched_count"]
            agg["bb"] += r["field_accuracy"]["blogger_name"] * r["expected_count"]
            agg["bt"] += r["field_accuracy"]["app_type"] * r["expected_count"]
            agg["ba"] += r["field_accuracy"]["amount"] * r["expected_count"]
            print(f"  段: 期望{r['expected_count']} 提取{r['extracted_count']} 全对{r['matched_count']} "
                  f"P={r['precision']:.2f} R={r['recall']:.2f} F1={r['f1']:.2f}")

        n = agg["expected"]
        precision = agg["matched"] / agg["extracted"] if agg["extracted"] else 0.0
        recall = agg["matched"] / n if n else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        print(f"  → 合计: P={precision:.2%} R={recall:.2%} F1={f1:.2%} "
              f"| 字段: 博主={agg['bb']/n:.2%} 类型={agg['bt']/n:.2%} 金额={agg['ba']/n:.2%}\n")

        report_lines += [
            f"## {mode_name}",
            "",
            f"| 指标 | 数值 |",
            f"|---|---|",
            f"| 期望条目 | {agg['expected']} |",
            f"| 提取条目 | {agg['extracted']} |",
            f"| 全对条目 | {agg['matched']} |",
            f"| 精确率 | {precision:.2%} |",
            f"| 召回率 | {recall:.2%} |",
            f"| F1 | {f1:.2%} |",
            f"| 博主名正确率 | {agg['bb']/n:.2%} |",
            f"| 类型正确率 | {agg['bt']/n:.2%} |",
            f"| 金额正确率 | {agg['ba']/n:.2%} |",
            "",
        ]

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))
    print(f"报告已写入: {REPORT_FILE}")


if __name__ == "__main__":
    main()
