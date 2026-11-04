"""Decky backend for Quest Tether: a client of qtetherd, never runs adb or the relay itself."""
import asyncio
import json

HOST = "127.0.0.1"
PORT = 31417
_TIMEOUT = 5.0


class Plugin:
    async def _send(self, cmd: str) -> dict:
        """Send one JSON command line to qtetherd and return its one-line JSON answer."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(HOST, PORT), timeout=_TIMEOUT
            )
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
            return {"state": "service-down"}
        try:
            writer.write((json.dumps({"cmd": cmd}) + "\n").encode())
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=_TIMEOUT)
        except (OSError, asyncio.TimeoutError):
            return {"state": "service-down"}
        finally:
            writer.close()
        try:
            return json.loads(line)
        except ValueError:
            return {"state": "service-down"}

    async def status(self) -> dict:
        """The service's status answer (state, temperature zone), or {"state": "service-down"}."""
        return await self._send("status")

    async def connect(self) -> dict:
        """Ask the service to connect the headset; its answer or {"state": "service-down"}."""
        return await self._send("connect")

    async def disconnect(self) -> dict:
        """Ask the service to disconnect the headset; its answer or {"state": "service-down"}."""
        return await self._send("disconnect")

    async def _main(self):
        pass

    async def _unload(self):
        pass
