"""Linux implementation of the platform layer (stub; see docs/code-map.md for what each call does on Linux)."""
from .base import Platform


class LinuxPlatform(Platform):
    name = "linux"
