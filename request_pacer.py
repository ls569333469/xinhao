"""Process-wide provider pacing; no network, credentials, or persistent state."""
import datetime as dt
import math
import threading
import time
from email.utils import parsedate_to_datetime


class RequestPacer:
    def __init__(self, limit_per_minute, utilization=.9, *, clock=None, sleep=None, wall_clock=None):
        if not (math.isfinite(limit_per_minute) and limit_per_minute > 0
                and math.isfinite(utilization) and 0 < utilization <= 1):
            raise ValueError('Positive request limit and utilization in (0, 1] required')
        self._interval = 60 / (limit_per_minute * utilization)
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._wall_clock = wall_clock or time.time
        self._lock = threading.Lock()
        self._next = 0.
        self._cooldown = 0.

    def wait(self, max_wait=None):
        # Recheck after sleeping: another thread may extend the cooldown.
        # Set the next slot from actual dispatch time, avoiding overdue-slot bursts.
        deadline = None if max_wait is None else self._clock() + max(0., max_wait)
        while True:
            with self._lock:
                now = self._clock()
                pause = max(self._next, self._cooldown) - now
                if pause <= 0:
                    self._next = now + self._interval
                    return
                if deadline is not None and now + pause > deadline:
                    raise TimeoutError('Provider cooldown exceeds verification time budget')
            self._sleep(min(pause, 60.))

    def rate_limited(self, retry_after=None):
        delay = 0.
        try:
            delay = float(retry_after)
        except (TypeError, ValueError):
            try:
                when = parsedate_to_datetime(str(retry_after))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=dt.timezone.utc)
                delay = when.timestamp() - self._wall_clock()
            except (TypeError, ValueError, OverflowError):
                pass
        if not math.isfinite(delay):
            delay = 0.
        with self._lock:
            self._interval *= 2
            delay = max(60., delay, self._interval)
            self._cooldown = max(self._cooldown, self._clock() + delay)
        return delay
