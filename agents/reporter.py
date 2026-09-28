"""
汇报生成 Agent
生成给负责人查看的简短汇报
"""
from typing import List, Dict
from datetime import datetime


class ReporterAgent:
    """汇报生成 Agent"""

    def generate_report(self, all_data: List[dict]) -> dict:
        """
        生成汇总汇报
        all_data: 所有已确认的申请数据
        """
        if not all_data:
            return {
                "title": "推广申请日报",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "summary": "今日暂无申请数据",
                "details": [],
                "alerts": [],
            }

        # 按类型分组
        douyin_apps = [d for d in all_data if d.get("app_type") == "douyin_ad"]
        advance_apps = [d for d in all_data if d.get("app_type") == "advance_payment"]

        # 计算总金额
        douyin_total = sum(d.get("amount", 0) for d in douyin_apps)
        advance_total = sum(d.get("amount", 0) for d in advance_apps)

        # 按博主汇总
        blogger_summary = self._group_by_blogger(all_data)

        # 按日期汇总
        date_summary = self._group_by_date(all_data)

        # 生成摘要
        today = datetime.now().strftime("%Y-%m-%d")
        summary_lines = [
            f"📊 推广申请日报 ({today})",
            "",
            f"🎵 抖加申请：{len(douyin_apps)} 笔，合计 ¥{douyin_total:,.2f}",
            f"💰 垫付申请：{len(advance_apps)} 笔，合计 ¥{advance_total:,.2f}",
            f"",
            f"📌 涉及博主：{len(blogger_summary)} 位",
        ]

        # 生成详细明细
        details = []
        for blogger, apps in blogger_summary.items():
            total = sum(a.get("amount", 0) for a in apps)
            types = set(a.get("app_type", "") for a in apps)
            type_str = "、".join([
                "抖加" if t == "douyin_ad" else "垫付"
                for t in types
            ])
            details.append({
                "blogger": blogger,
                "total_amount": total,
                "count": len(apps),
                "types": type_str,
            })

        # 生成异常提醒
        alerts = []
        if douyin_total == 0 and advance_total == 0:
            alerts.append("⚠️ 今日无任何申请数据")

        return {
            "title": "推广申请日报",
            "date": today,
            "summary": "\n".join(summary_lines),
            "douyin_total": douyin_total,
            "advance_total": advance_total,
            "douyin_count": len(douyin_apps),
            "advance_count": len(advance_apps),
            "details": sorted(details, key=lambda x: x["total_amount"], reverse=True),
            "alerts": alerts,
        }

    def _group_by_blogger(self, data: List[dict]) -> Dict[str, List[dict]]:
        """按博主分组"""
        result = {}
        for d in data:
            blogger = d.get("blogger_name", "未知")
            if blogger not in result:
                result[blogger] = []
            result[blogger].append(d)
        return result

    def _group_by_date(self, data: List[dict]) -> Dict[str, List[dict]]:
        """按日期分组"""
        result = {}
        for d in data:
            date = d.get("apply_date", "未知")
            if date not in result:
                result[date] = []
            result[date].append(d)
        return result

    def generate_text_report(self, report: dict) -> str:
        """生成纯文本汇报"""
        lines = [
            f"📋 {report['title']}",
            f"📅 {report['date']}",
            "",
            "=" * 40,
            "",
            report["summary"],
            "",
            "=" * 40,
            "📝 博主明细：",
            "",
        ]
        for i, d in enumerate(report["details"][:20], 1):
            lines.append(f"{i}. {d['blogger']}：¥{d['total_amount']:,.2f} ({d['count']}笔，{d['types']})")

        if report["alerts"]:
            lines.extend([
                "",
                "=" * 40,
                "⚠️ 异常提醒：",
                "",
            ])
            for alert in report["alerts"]:
                lines.append(f"  {alert}")

        return "\n".join(lines)
