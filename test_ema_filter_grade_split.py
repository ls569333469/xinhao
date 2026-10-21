# -*- coding: utf-8 -*-
"""Current EMA parent/level behavior and empty-store acceptance."""
from __future__ import annotations

import tempfile
import unittest
import subprocess
from pathlib import Path

from ema_squeeze_store import EmaSqueezeStore


class GradeSplitLogic(unittest.TestCase):
    def test_actual_both_page_filters(self):
        root = Path(__file__).resolve().parent
        result = subprocess.run(["node", str(root / "verify-filters.cjs")], cwd=root,
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_persist_writes_empty_store(self):
        with tempfile.TemporaryDirectory() as td:
            store = EmaSqueezeStore(Path(td))
            meta = store.persist()
            path = Path(meta["path"])
            self.assertTrue(path.exists())
            self.assertEqual(meta["active"], 0)
            self.assertEqual(meta["cycles"], 0)
            raw = path.read_text(encoding="utf-8")
            self.assertIn("ema-squeeze-candidates-v1", raw)


if __name__ == "__main__":
    unittest.main()
