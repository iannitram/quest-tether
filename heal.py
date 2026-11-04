"""The heal ladder and flap breaker for the health loop.

Pure state machine: ``now`` is passed in, nothing sleeps, and every call
returns what the caller should do. The manager keeps the ADB and process
work; this module only decides.
"""

from collections import deque
from enum import Enum


MISS_LIMIT = 3
MIN_HEAL_GAP_SECS = 30.0
FLAP_WINDOW_SECS = 600.0
FLAP_MAX = 6
MAX_RECONNECTS = 10


class Heal(Enum):
    NONE = "none"
    RESTART_CLIENT = "restart_client"
    RESTART_ADB_THEN_CLIENT = "restart_adb_then_client"
    FULL_RECONNECT = "full_reconnect"
    GIVE_UP = "give_up"


class Healer:
    """Count tunnel misses, climb the heal ladder, and trip the breakers."""

    def __init__(
        self,
        miss_limit: int = MISS_LIMIT,
        min_heal_gap: float = MIN_HEAL_GAP_SECS,
        flap_max: int = FLAP_MAX,
        flap_window: float = FLAP_WINDOW_SECS,
        max_reconnects: int = MAX_RECONNECTS,
    ):
        self.miss_limit = miss_limit
        self.min_heal_gap = min_heal_gap
        self.flap_max = flap_max
        self.flap_window = flap_window
        self.max_reconnects = max_reconnects
        self._misses = 0
        self._stage = 0
        self._last_heal = None
        self._heals = deque()
        self._reconnects = 0
        self._tripped = False
        self._trip_reason = None

    def reset(self):
        """Start a new session.

        Keeps the last heal time: the manager has always let the 30 s spacing
        carry across a quick disconnect/reconnect.
        """
        self._misses = 0
        self._stage = 0
        self._heals.clear()
        self._reconnects = 0
        self._tripped = False
        self._trip_reason = None

    def observe(self, now: float, relay_alive: bool, tun0: bool) -> Heal:
        """Look at one health reading and return the next action."""
        if self._tripped:
            return Heal.GIVE_UP

        if relay_alive and tun0:
            self._misses = 0
            self._stage = 0
            return Heal.NONE

        self._misses += 1
        if self._misses < self.miss_limit:
            return Heal.NONE

        if self._last_heal is not None and now - self._last_heal < self.min_heal_gap:
            return Heal.NONE

        heal = self._next_heal()
        self.note_recovery(now)
        if self._tripped:
            return Heal.GIVE_UP

        if heal is Heal.FULL_RECONNECT and self._reconnects >= self.max_reconnects:
            self._tripped = True
            self._trip_reason = "reconnects"
            return Heal.GIVE_UP

        self._last_heal = now
        if heal is Heal.FULL_RECONNECT:
            self._reconnects += 1
        self._misses = 0
        self._stage = (self._stage + 1) % 3
        return heal

    def note_recovery(self, now: float) -> int:
        """Record a recovery made outside the ladder (for example a reconnect
        after the relay died); return how many are inside the flap window."""
        self._prune(now)
        self._heals.append(now)
        if len(self._heals) > self.flap_max and not self._tripped:
            self._tripped = True
            self._trip_reason = "flap"
        return len(self._heals)

    def reset_after_reconnect(self):
        """A reconnect brought the tunnel back: clear the miss and reconnect
        counts, but keep the ladder stage and the flap window."""
        self._misses = 0
        self._reconnects = 0

    @property
    def tripped(self) -> bool:
        return self._tripped

    @property
    def trip_reason(self):
        return self._trip_reason

    @property
    def reconnects(self) -> int:
        return self._reconnects

    def _next_heal(self) -> Heal:
        if self._stage == 0:
            return Heal.RESTART_CLIENT
        if self._stage == 1:
            return Heal.RESTART_ADB_THEN_CLIENT
        return Heal.FULL_RECONNECT

    def _prune(self, now: float):
        while self._heals and now - self._heals[0] >= self.flap_window:
            self._heals.popleft()
