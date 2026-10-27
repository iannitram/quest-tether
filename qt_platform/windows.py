"""Windows implementation of the platform layer (stub; see docs/code-map.md for what each call does on Windows)."""
from .base import Platform


class WindowsPlatform(Platform):
    name = "windows"
