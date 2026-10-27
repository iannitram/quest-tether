"""Decky backend for Quest Tether (stub): a client of qtetherd, never runs adb or the relay itself."""


class Plugin:
    async def status(self) -> dict:
        """The service's status answer (state, temperature zone), or {"state": "service-down"}."""
        raise NotImplementedError

    async def connect(self) -> dict:
        raise NotImplementedError

    async def disconnect(self) -> dict:
        raise NotImplementedError

    async def _main(self):
        pass

    async def _unload(self):
        pass
