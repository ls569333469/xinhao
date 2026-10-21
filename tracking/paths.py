"""Resolve contract and package paths without touching production."""
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
SUBMISSION_ROOT = PACKAGE_ROOT.parent
S1_CONTRACT_ROOT = PACKAGE_ROOT / "contract"
DEFAULT_LEDGER_PATH = SUBMISSION_ROOT / "ledger_data" / "isolated-tracking.sqlite3"
