"""Decky backend forwards to qtetherd (no headset, no adb): python -m unittest discover tests"""
import asyncio
import importlib.util
import json
import os
import socket
import unittest

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "decky", "main.py")
_spec = importlib.util.spec_from_file_location("decky_main", _PATH)
decky_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(decky_main)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ForwardsToService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received = []

        async def handle(reader, writer):
            self.received.append(await reader.readline())
            writer.write(b'{"state": "connected"}\n')
            await writer.drain()
            writer.close()

        self.server = await asyncio.start_server(handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self._old_port = decky_main.PORT
        decky_main.PORT = self.port

    async def asyncTearDown(self):
        decky_main.PORT = self._old_port
        self.server.close()
        await self.server.wait_closed()

    async def test_status_sends_one_json_line_and_returns_answer(self):
        self.assertEqual(await decky_main.Plugin().status(), {"state": "connected"})
        self.assertEqual([json.loads(x) for x in self.received], [{"cmd": "status"}])
        self.assertTrue(self.received[0].endswith(b"\n"))

    async def test_connect_sends_connect_command(self):
        self.assertEqual(await decky_main.Plugin().connect(), {"state": "connected"})
        self.assertEqual([json.loads(x) for x in self.received], [{"cmd": "connect"}])

    async def test_disconnect_sends_disconnect_command(self):
        self.assertEqual(await decky_main.Plugin().disconnect(), {"state": "connected"})
        self.assertEqual([json.loads(x) for x in self.received], [{"cmd": "disconnect"}])


class ServiceDown(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_port = decky_main.PORT
        decky_main.PORT = _free_port()  # nothing listens here

    async def asyncTearDown(self):
        decky_main.PORT = self._old_port

    async def test_status_refused_gives_service_down(self):
        self.assertEqual(await decky_main.Plugin().status(), {"state": "service-down"})

    async def test_connect_refused_gives_service_down(self):
        self.assertEqual(await decky_main.Plugin().connect(), {"state": "service-down"})

    async def test_disconnect_refused_gives_service_down(self):
        self.assertEqual(await decky_main.Plugin().disconnect(), {"state": "service-down"})


class Defaults(unittest.TestCase):
    def test_default_port_is_31417(self):
        self.assertEqual(decky_main.PORT, 31417)


if __name__ == "__main__":
    unittest.main()
