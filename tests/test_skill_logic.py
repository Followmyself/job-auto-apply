#!/usr/bin/env python3
"""不连接浏览器、不写入真实配置的纯逻辑回归测试。"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from boss_apply import CHAT_QUOTA_RE, BossApplier, decode_salary, is_match as boss_is_match  # noqa: E402
from common import city_rank  # noqa: E402
from job51_apply import is_match as job51_is_match, parse_salary_51  # noqa: E402


class SkillLogicTests(unittest.TestCase):
    def test_boss_salary(self):
        self.assertEqual(decode_salary("6-8K·24薪"), (6.0, 8.0, 16.0))

    def test_job51_mixed_units_salary(self):
        self.assertEqual(parse_salary_51("9千-1.4万"), (9.0, 14.0, 14.0))
        self.assertEqual(parse_salary_51("1-1.5万/月"), (10.0, 15.0, 15.0))

    def test_city_priority(self):
        cities = ["长沙", "武汉", "广州"]
        self.assertEqual(city_rank("广州 AI 产品经理", cities), 2)
        self.assertEqual(city_rank("远程岗位", cities), 3)

    def test_title_filters(self):
        self.assertTrue(boss_is_match("AI应用开发工程师"))
        self.assertFalse(boss_is_match("AI产品销售代表"))
        self.assertTrue(job51_is_match("Python开发工程师"))

    def test_missing_resume_is_not_treated_as_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = {
                "user": {"pdf_resume": ""},
                "boss": {"port": 9233},
                "logs_dir": temp_dir,
            }
            applier = BossApplier(cfg)
            self.assertFalse(asyncio.run(applier.upload_pdf()))

    def test_chat_quota_warning_is_parsed(self):
        match = CHAT_QUOTA_RE.search("温馨提示：您今天已与120位BOSS沟通，还剩30次沟通机会哦")
        self.assertIsNotNone(match)
        self.assertEqual((int(match.group(1)), int(match.group(2))), (120, 30))

    def test_chat_quota_warning_is_dismissed_before_upload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = {
                "user": {"pdf_resume": ""},
                "boss": {"port": 9233},
                "logs_dir": temp_dir,
            }
            applier = BossApplier(cfg)
            calls = []

            async def fake_ev(expression):
                calls.append(expression)
                return "您今天已与120位BOSS沟通，还剩30次沟通机会哦" if len(calls) == 1 else "clicked"

            applier.ev = fake_ev
            result = asyncio.run(applier.dismiss_chat_quota_warning())
            self.assertEqual(result["remaining"], 30)
            self.assertTrue(result["clicked"])
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
