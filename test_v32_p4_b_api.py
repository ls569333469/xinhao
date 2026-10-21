# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import io
import tempfile
import unittest
from pathlib import Path

from b_accumulation_store import BAccumulationStore
from b_observe_api import events_csv, events_payload


class P4Api(unittest.TestCase):
    def test_list_and_csv(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = BAccumulationStore(root)
            store.ensure_observation_start("2026-01-01T00:00:00+00:00")
            store.upsert_event(
                market="USDⓈ-M",
                symbol="CCCUSDT",
                trigger_open_time=1699999200000,
                payload={
                    "technical_event": "pass",
                    "technical_confirmation": "pending",
                    "pending_confirm": True,
                    "event_ratio": 9.0,
                    "confirm_ratio": None,
                    "abs_return_ok": True,
                    "input_fingerprint": "abc",
                },
            )
            payload = events_payload(root)
            self.assertEqual(payload["event_count"], 1)
            self.assertEqual(payload["pool"], "B_ACCUMULATION_1H")
            csv_text = events_csv(root)
            rows = list(csv.DictReader(io.StringIO(csv_text)))
            self.assertEqual(rows[0]["symbol"], "CCCUSDT")


if __name__ == "__main__":
    unittest.main()
