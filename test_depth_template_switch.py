# -*- coding: utf-8 -*-
"""Regression: quick/standard/deep remain distinct; migration does not copy B into quick/deep."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from ai_research import LEGACY_STANDARD_INSTRUCTION
from config_store import ConfigStore

ROOT = Path(__file__).resolve().parent

# Chinese literals via escapes to survive non-UTF8 tool pipes
TPL_B = "\u6a21\u677f B"  # 模板 B
LABEL_TIER = "\u6863\u4f4d\u6a21\u677f"  # 档位模板
LABEL_OLD_DEPTH = "\u5f53\u524d\u6df1\u5ea6\u6a21\u677f"  # 当前深度模板


class DepthTemplateSwitchTests(unittest.TestCase):
    def test_catalog_only_offers_standard_b(self):
        data = json.loads((ROOT / "design" / "prompt-templates.json").read_text(encoding="utf-8"))
        self.assertEqual(set(data["profiles"]), {"standard"})
        self.assertEqual(data["profiles"]["standard"]["template"], "B")
        self.assertIn(TPL_B, data["profiles"]["standard"]["instruction"])

    def test_prototype_prompt_data_matches_catalog(self):
        html = (ROOT / "design" / "prototype.html").read_text(encoding="utf-8")
        m = re.search(r'<script[^>]*id=["\']prompt-data["\'][^>]*>(.*?)</script>', html, re.S)
        self.assertTrue(m)
        embedded = json.loads(m.group(1).strip())
        catalog = json.loads((ROOT / "design" / "prompt-templates.json").read_text(encoding="utf-8"))
        for key in ("standard",):
            self.assertEqual(
                embedded["profiles"][key]["instruction"],
                catalog["profiles"][key]["instruction"],
            )
        self.assertNotIn('name="depth" value="quick"', html)
        self.assertNotIn('name="depth" value="deep"', html)
        self.assertIn('id="depthPromptLabel"', html)
        self.assertIn("updateDepthTemplateLabels", html)
        self.assertNotIn(LABEL_OLD_DEPTH, html)

    def test_migrate_standard_does_not_touch_quick_deep(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConfigStore(Path(tmp))
            catalog = json.loads((ROOT / "design" / "prompt-templates.json").read_text(encoding="utf-8"))
            new_b = catalog["profiles"]["standard"]["instruction"]
            store.set_prompt_edits({
                "overrides": {
                    "standard": LEGACY_STANDARD_INSTRUCTION,
                    "quick": "KEEP_QUICK_CUSTOM",
                    "deep": "KEEP_DEEP_CUSTOM",
                },
                "base_version": "research-prompts-v1.0",
                "replace": True,
            })
            result = store.migrate_unmodified_standard_prompt(new_b)
            self.assertEqual(result["action"], "migrated_default_to_template_b")
            overrides = store.get_prompt_edits()["overrides"]
            self.assertEqual(overrides["standard"], new_b)
            self.assertEqual(overrides["quick"], "KEEP_QUICK_CUSTOM")
            self.assertEqual(overrides["deep"], "KEEP_DEEP_CUSTOM")


if __name__ == "__main__":
    unittest.main()
