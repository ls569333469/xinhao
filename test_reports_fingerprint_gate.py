"""AI POST /api/reports must hard-gate strategy fingerprint like local reports."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rule_export_gate import stamp_export_payload
from rule_bindings import BindingConflict


class FakeBindingsUnbound:
    def list_bindings(self):
        return {'surfaces': {'reports': {'bound': False}, 'market': {'bound': False}}}


class FakeBindingsBound:
    def list_bindings(self):
        ref = {
            'strategy_id': 'S-t',
            'version_id': 'ver-aaaaaaaaaaaa',
            'version': '1.0.0',
            'content_hash': 'abc123',
            'registry_hash': 'reg',
            'unified_key': 'S-t@1.0.0#abc123',
            'id_version_hash': 'ivh',
        }
        return {'surfaces': {'reports': {'bound': True, 'ref': ref}, 'market': {'bound': False}}}


class ReportsFingerprintGateTests(unittest.TestCase):
    def test_unbound_raises(self):
        with self.assertRaises(BindingConflict):
            stamp_export_payload(FakeBindingsUnbound(), {'contracts': [{'symbol': 'BTCUSDT'}]})

    def test_bound_stamps(self):
        out, gate = stamp_export_payload(FakeBindingsBound(), {'contracts': [{'symbol': 'BTCUSDT'}], 'depth': 'quick'})
        self.assertTrue(gate['bound'])
        self.assertIsNotNone(out.get('strategy_fingerprint'))
        self.assertEqual(out['unified_key'], 'S-t@1.0.0#abc123')


if __name__ == '__main__':
    unittest.main()
