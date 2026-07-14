"""
Quest Tether — Decky Loader backend.

Runs inside Decky's Python (Game Mode). Reuses the validated questtether engine
to drive the reverse tether, and exposes async methods to the Quick-Access panel.

The blocking engine calls (connect ~2-20s, disconnect ~15-25s) run in a thread
executor so the asyncio loop stays responsive; a 1s loop broadcasts status to the
frontend. Device presence is cached from the ADB monitor thread so the broadcast
never blocks on an adb subprocess.
"""
import os
import sys
import time
import asyncio

import decky

# adb reads $HOME/.android/adbkey for the key it presents to the Quest. Make sure
# it's the deck user's *authorized* key, not whatever HOME the Decky service had.
os.environ["HOME"] = decky.DECKY_USER_HOME
os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")

# Reuse the engine + bundled tools from the repo checkout (override via env).
REPO = os.environ.get("QUESTTETHER_REPO",
                      os.path.join(decky.DECKY_USER_HOME, "dev", "quest-tether"))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from questtether.adb import ADBManager           # noqa: E402
from questtether.tether import TetherManager, TetherState  # noqa: E402


class Plugin:
    async def _main(self):
        self.loop = asyncio.get_event_loop()
        self.adb = ADBManager()
        self.tether = TetherManager(self.adb)
        self.busy = False          # a connect/disconnect is in flight
        self.auto = True           # auto-connect when the Quest is plugged in
        self.stop = False
        self.adb_ok = self.adb.is_adb_available()
        self.quest = None          # cached {serial,status,model} from the monitor
        self.adb.start_monitoring(on_connected=self._dev_connected,
                                  on_disconnected=self._dev_disconnected)
        self.loop.create_task(self._broadcast_loop())
        decky.logger.info(f"Quest Tether backend ready (engine={REPO}, adb_ok={self.adb_ok})")

    async def _unload(self):
        self.stop = True
        try:
            self.adb.stop_monitoring()
        except Exception:
            pass
        if self._active():
            try:
                await self.loop.run_in_executor(None, self.tether.disconnect)
            except Exception as e:
                decky.logger.error(f"unload disconnect error: {e}")
        decky.logger.info("Quest Tether backend unloaded")

    # --- ADB device monitor (fires on a background thread) ---

    def _dev_connected(self, info):
        self.quest = {
            "serial": info.get("serial", ""),
            "status": "unauthorized" if info.get("_unauthorized") else info.get("status", "device"),
            "model": info.get("model", "Quest"),
        }
        if (self.auto and not self.busy and not self._active()
                and not info.get("_unauthorized")):
            self.loop.call_soon_threadsafe(
                lambda: self.loop.create_task(self._auto_connect()))

    def _dev_disconnected(self):
        self.quest = None

    async def _auto_connect(self):
        if self.busy or self._active():
            return
        decky.logger.info("Auto-connect: Quest detected")
        await self.connect()

    def _active(self) -> bool:
        return bool(self.tether._running) or self.tether.stats.state == TetherState.CONNECTED

    # --- Frontend-callable methods ---

    async def connect(self):
        if self.busy:
            return {"ok": False, "error": "busy"}
        quest = await self.loop.run_in_executor(None, self.adb.find_quest)
        if not quest or quest.get("status") != "device":
            status = quest.get("status") if quest else "none"
            return {"ok": False, "error": f"no authorized Quest ({status})"}
        self.busy = True
        try:
            ok = await self.loop.run_in_executor(None, self.tether.connect, quest["serial"])
            return {"ok": bool(ok), "error": "" if ok else self.tether.stats.last_error}
        except Exception as e:
            decky.logger.error(f"connect error: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            self.busy = False

    async def disconnect(self):
        if self.busy:
            return {"ok": False, "error": "busy"}
        self.busy = True
        try:
            await self.loop.run_in_executor(None, self.tether.disconnect)
            return {"ok": True, "error": ""}
        except Exception as e:
            decky.logger.error(f"disconnect error: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            self.busy = False

    async def set_auto(self, enabled: bool):
        self.auto = bool(enabled)
        return {"ok": True, "auto": self.auto}

    async def get_status(self):
        return self._status()

    def _status(self):
        s = self.tether.stats
        q = self.quest
        start = self.tether._start_time
        connected = s.state == TetherState.CONNECTED
        uptime = (time.time() - start) if (start and connected) else 0.0
        return {
            "state": s.state.value,
            "connected": connected,
            "busy": self.busy,
            "auto": self.auto,
            "adb_ok": self.adb_ok,
            "quest_present": q is not None,
            "quest_status": q["status"] if q else "",
            "model": (s.device_model or (q["model"] if q else "")) or "",
            "rx_rate": s.rx_rate_bps,
            "tx_rate": s.tx_rate_bps,
            "rx_bytes": s.rx_bytes,
            "tx_bytes": s.tx_bytes,
            "uptime": uptime,
            "reconnects": s.reconnect_count,
            "error": s.last_error,
        }

    async def _broadcast_loop(self):
        while not self.stop:
            try:
                await decky.emit("tether_status", self._status())
            except Exception as e:
                decky.logger.error(f"broadcast error: {e}")
            await asyncio.sleep(1.0)
