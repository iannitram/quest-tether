"""Disk-backed crash upload limits and reports awaiting a connection."""

import json
import os
from typing import List


class Limiter:
    def __init__(self, path: str, per_day: int = 5):
        self.path = path
        self.per_day = per_day
        try:
            with open(path, encoding="utf-8") as stream:
                times = json.load(stream)
            if not isinstance(times, list) or any(
                type(t) not in (int, float) for t in times
            ):
                times = []
        except (OSError, ValueError):
            times = []
        self.times: List[float] = times

    def allow(self, now: float) -> bool:
        """Check the rolling day budget without recording an upload."""
        return sum(now - t < 86400.0 for t in self.times) < self.per_day

    def record(self, now: float) -> None:
        self.times.append(now)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as stream:
            json.dump(self.times, stream)


class Queue:
    def __init__(self, folder: str, max_items: int = 10):
        self.folder = folder
        self.max_items = max_items

    def add(self, name: str, data: bytes) -> None:
        os.makedirs(self.folder, exist_ok=True)
        with open(os.path.join(self.folder, name), "wb") as stream:
            stream.write(data)
        pending = self.pending()
        for oldest in pending[:max(0, len(pending) - self.max_items)]:
            self.remove(oldest)

    def pending(self) -> List[str]:
        """Return queued file names in timestamp name order."""
        try:
            return sorted(
                name for name in os.listdir(self.folder)
                if os.path.isfile(os.path.join(self.folder, name))
            )
        except FileNotFoundError:
            return []

    def read(self, name: str) -> bytes:
        with open(os.path.join(self.folder, name), "rb") as stream:
            return stream.read()

    def remove(self, name: str) -> None:
        try:
            os.remove(os.path.join(self.folder, name))
        except FileNotFoundError:
            pass
