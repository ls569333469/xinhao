"""Portable IANA timezone fallback; no network, install or global environment edits."""
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

BUNDLED = Path(__file__).resolve().parent / 'vendor' / 'tzdata-2026.5.zip'
if BUNDLED.is_file() and str(BUNDLED) not in sys.path:
    # System timezone data and an installed tzdata package retain priority.
    sys.path.append(str(BUNDLED))

def ensure_timezone_data():
    try:
        for name in ('Asia/Shanghai', 'UTC', 'America/New_York', 'Europe/London'):
            ZoneInfo(name)
    except Exception as exc:
        raise RuntimeError('缺少IANA时区数据；请恢复项目vendor/tzdata-2026.5.zip或安装tzdata。') from exc
