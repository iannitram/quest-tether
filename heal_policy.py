"""Pure awake-gated heal policy for the headset liveness loop."""

# Policy re-implemented from quest-vd-wired (Apache-2.0), host-rust/crates/gnirehtet-vd/src/runtime.rs AndroidLiveness.
import dataclasses
from dataclasses import dataclass

GRACE = 10.0
AWAKE_PROBE_INTERVAL = 5.0
ASLEEP_PROBE_INTERVAL = 15.0
INITIAL_BACKOFF = 60.0
MAX_BACKOFF = 300.0


def parse_wakefulness(text: str | None) -> bool | None:
    """True if the first line whose stripped form starts with "mWakefulness=" has value "Awake" (value stripped), False for any other value, None if no such line (or text is None/empty)."""
    if not text:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("mWakefulness="):
            value = stripped[len("mWakefulness="):].strip()
            return value == "Awake"
    return None


def restart_backoff(restarts: int) -> float:
    """INITIAL_BACKOFF * 2 ** min(max(restarts - 1, 0), 8), capped at MAX_BACKOFF."""
    exponent = min(max(restarts - 1, 0), 8)
    return min(INITIAL_BACKOFF * 2 ** exponent, MAX_BACKOFF)


@dataclass(frozen=True)
class Liveness:
    silent_since: float | None = None
    awake_since: float | None = None
    next_probe_at: float | None = None
    next_restart_at: float | None = None
    restarts: int = 0

    def connected(self):
        """Everything reset."""
        return Liveness()

    def ineligible(self):
        """Same restarts and next_restart_at, but silent_since, awake_since, next_probe_at = None."""
        return dataclasses.replace(
            self,
            silent_since=None,
            awake_since=None,
            next_probe_at=None,
        )

    def silent(self, now):
        """silent_since = existing silent_since if not None, else now."""
        return dataclasses.replace(
            self,
            silent_since=self.silent_since if self.silent_since is not None else now,
        )

    def probe_due(self, now):
        """True if next_probe_at is None or now >= next_probe_at."""
        return self.next_probe_at is None or now >= self.next_probe_at

    def observed(self, awake, now):
        """Update the awake timestamp and schedule the next probe."""
        awake_since = (
            self.awake_since if self.awake_since is not None else now
        ) if awake else None
        interval = AWAKE_PROBE_INTERVAL if awake else ASLEEP_PROBE_INTERVAL
        return dataclasses.replace(
            self,
            awake_since=awake_since,
            next_probe_at=now + interval,
        )

    def restart_due(self, now):
        """True only if both silent and awake for the grace, and the backoff has elapsed."""
        if self.silent_since is None:
            return False
        if self.awake_since is None:
            return False
        if now - self.silent_since < GRACE:
            return False
        if now - self.awake_since < GRACE:
            return False
        if self.next_restart_at is not None and now < self.next_restart_at:
            return False
        return True

    def restarted(self, now):
        """Record a restart and start a fresh observation cycle."""
        restarts = self.restarts + 1
        return dataclasses.replace(
            self,
            restarts=restarts,
            silent_since=now,
            awake_since=None,
            next_probe_at=now + AWAKE_PROBE_INTERVAL,
            next_restart_at=now + restart_backoff(restarts),
        )
