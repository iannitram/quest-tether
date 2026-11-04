"""qtetherd JSON-lines server and client over loopback (no headset): python -m unittest discover -s tests"""
import json
import socket
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import qtetherd  # noqa: E402


def start(handler):
    server = qtetherd.make_server(handler, "127.0.0.1", 0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    def stop():
        server.shutdown()
        server.server_close()
        t.join(5)
    return server, stop


def raw(port, data: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(data)
        return s.makefile("rb").readline()


class RoundTrip(unittest.TestCase):
    def setUp(self):
        self.seen = []

        def handler(req):
            self.seen.append(req)
            return {"state": "connected", "echo": req}
        self.server, stop = start(handler)
        self.addCleanup(stop)
        self.port = self.server.server_address[1]

    def test_request_returns_the_handler_answer(self):
        answer = qtetherd.request({"cmd": "status"}, "127.0.0.1", self.port)
        self.assertEqual(answer, {"state": "connected", "echo": {"cmd": "status"}})
        self.assertEqual(self.seen, [{"cmd": "status"}])

    def test_the_wire_format_is_one_json_line_each_way(self):
        line = raw(self.port, b'{"cmd": "connect"}\n')
        self.assertTrue(line.endswith(b"\n"))
        self.assertEqual(json.loads(line), {"state": "connected", "echo": {"cmd": "connect"}})

    def test_several_requests_in_a_row(self):
        for cmd in ("status", "connect", "disconnect"):
            qtetherd.request({"cmd": cmd}, "127.0.0.1", self.port)
        self.assertEqual([r["cmd"] for r in self.seen], ["status", "connect", "disconnect"])

    def test_bad_json_is_answered_without_calling_the_handler(self):
        self.assertEqual(json.loads(raw(self.port, b"not json\n")), {"ok": False, "error": "bad request"})
        self.assertEqual(self.seen, [])


class HandlerFailure(unittest.TestCase):
    def test_exception_in_handler_gives_internal_error_and_the_server_lives(self):
        calls = []

        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                raise RuntimeError("boom")
            return {"ok": True}
        server, stop = start(handler)
        self.addCleanup(stop)
        port = server.server_address[1]
        self.assertEqual(qtetherd.request({"cmd": "x"}, "127.0.0.1", port), {"ok": False, "error": "internal error"})
        self.assertEqual(qtetherd.request({"cmd": "x"}, "127.0.0.1", port), {"ok": True})


class Binding(unittest.TestCase):
    def test_binds_only_to_loopback(self):
        server, stop = start(lambda r: {})
        self.addCleanup(stop)
        self.assertEqual(server.server_address[0], "127.0.0.1")

    def test_other_hosts_are_refused(self):
        for host in ("0.0.0.0", "", "192.168.1.5"):
            with self.assertRaises(ValueError):
                qtetherd.make_server(lambda r: {}, host, 0)

    def test_second_service_on_the_same_port_refuses_to_start(self):
        server, stop = start(lambda r: {})
        self.addCleanup(stop)
        with self.assertRaises(OSError):
            qtetherd.make_server(lambda r: {}, "127.0.0.1", server.server_address[1])

    def test_default_port(self):
        self.assertEqual((qtetherd.HOST, qtetherd.PORT), ("127.0.0.1", 31417))


class ClientErrors(unittest.TestCase):
    def test_nothing_listening_raises_oserror(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(OSError):
            qtetherd.request({"cmd": "status"}, "127.0.0.1", port, timeout=2)

    def test_server_that_closes_without_answer_raises_oserror(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)
        threading.Thread(target=lambda: srv.accept()[0].close(), daemon=True).start()
        with self.assertRaises(OSError):
            qtetherd.request({"cmd": "status"}, "127.0.0.1", srv.getsockname()[1], timeout=2)


if __name__ == "__main__":
    unittest.main()
