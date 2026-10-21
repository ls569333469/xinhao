# -*- coding: utf-8 -*-
"""R8: Template B opening must require cutoff; bare 当前价格 is banned in the instruction."""
from __future__ import annotations
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class OpeningPromptTests(unittest.TestCase):
    def templates(self):
        return json.loads((ROOT / "design" / "prompt-templates.json").read_text(encoding="utf-8"))

    def test_standard_instruction_bans_bare_current_price(self):
        ins = self.templates()["profiles"]["standard"]["instruction"]
        self.assertIn("快照价格（截至", ins)
        self.assertIn("禁止单独写「当前价格/当前价/现价」", ins)
        # Old mandatory bare form must be gone
        self.assertNotIn("只留标题 + 当前价格：$X", ins)

    def test_common_reinforces_cutoff(self):
        common = self.templates()["common"]
        self.assertIn("禁止无截止时间的「当前价格/当前价/现价」", common)

if __name__ == "__main__":
    unittest.main()
