"""
幂等防重单元测试
================
- 同一文本重复提交：pending 拦截、confirmed 拦截、驳回后可重提
- pending 超过 30 分钟过期后可重新处理
- 指纹持久化（重启不丢）
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents import master as master_module
from agents.master import MasterAgent, PENDING_EXPIRE_SECONDS


class IdempotencyTestCase(unittest.TestCase):
    def setUp(self):
        # 隔离：替换类属性数据路径到临时目录，避免污染真实 data/
        self._tmp = tempfile.mkdtemp(prefix="promo_idem_test_")
        self._old_records = MasterAgent.records_file
        self._old_processed = MasterAgent.processed_file
        MasterAgent.records_file = os.path.join(self._tmp, "records.json")
        MasterAgent.processed_file = os.path.join(self._tmp, "processed_messages.json")
        self.m = MasterAgent()

    def tearDown(self):
        MasterAgent.records_file = self._old_records
        MasterAgent.processed_file = self._old_processed
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_hash_stability(self):
        h1 = self.m._text_hash("  申请A  ")
        h2 = self.m._text_hash("申请A")
        self.assertEqual(h1, h2)  # 首尾空白不影响指纹
        self.assertNotEqual(h1, self.m._text_hash("申请B"))

    def test_pending_blocks_resubmit(self):
        self.m.mark_processed("申请文本X", status="pending")
        dup = self.m.check_duplicate("申请文本X")
        self.assertIsNotNone(dup)
        self.assertTrue(dup["duplicate"])
        self.assertIn("等待确认", dup["message"])

    def test_confirmed_blocks_resubmit(self):
        self.m.mark_processed("申请文本Y", status="pending")
        h = self.m._text_hash("申请文本Y")
        self.m.mark_confirmed_hash(h)
        dup = self.m.check_duplicate("申请文本Y")
        self.assertIsNotNone(dup)
        self.assertIn("处理并写入", dup["message"])

    def test_rejected_allows_resubmit(self):
        self.m.mark_processed("申请文本Z", status="pending")
        h = self.m._text_hash("申请文本Z")
        self.m.mark_rejected_hash(h)
        self.assertIsNone(self.m.check_duplicate("申请文本Z"))

    def test_pending_expire(self):
        self.m.mark_processed("申请文本W", status="pending")
        # 手动把时间改成超时
        h = self.m._text_hash("申请文本W")
        self.m._processed[h]["time"] = time.time() - PENDING_EXPIRE_SECONDS - 1
        self.m._save_processed()
        self.assertIsNone(self.m.check_duplicate("申请文本W"))

    def test_persistence_across_reload(self):
        self.m.mark_processed("申请文本P", status="confirmed")
        m2 = MasterAgent()  # 模拟重启，重新加载
        dup = m2.check_duplicate("申请文本P")
        self.assertIsNotNone(dup)
        self.assertIn("处理并写入", dup["message"])

    def test_process_text_duplicate_short_circuit(self):
        """process_text 对已确认文本直接返回 duplicate，不再走提取"""
        self.m.mark_processed("一条已处理的申请", status="confirmed")
        result = self.m.process_text("一条已处理的申请")
        self.assertTrue(result.get("duplicate"))
        self.assertIsNone(result.get("extracted"))


if __name__ == "__main__":
    unittest.main()
