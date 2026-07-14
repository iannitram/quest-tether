"""
Quest Tether — native desktop GUI (Desktop Mode on SteamOS; also runs on Windows).

Pure tkinter/tk — zero external dependencies, so it runs on a stock SteamOS
Python with nothing to pip-install. It is a thin frontend over the validated
engine (ADBManager + TetherManager): all tether logic lives there, this file is
only widgets + wiring.

Engine callbacks fire on background threads; every one marshals back onto the Tk
main thread via ``self.after(0, ...)`` before touching a widget.
"""

import os
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

from . import __version__
from .adb import ADBManager
from .tether import TetherManager, TetherState, TetherStats
from .cli import format_bytes, format_rate, format_uptime


# --- Palette (carried over from the Windows build) ---
COLORS = {
    "bg_dark": "#1a1a2e",
    "bg_card": "#16213e",
    "bg_card_alt": "#0f3460",
    "accent": "#e94560",
    "accent_green": "#00d97e",
    "accent_yellow": "#f0c929",
    "accent_blue": "#4cc9f0",
    "text_primary": "#ffffff",
    "text_secondary": "#a0a0b8",
    "text_dim": "#6c6c80",
    "border": "#2a2a4a",
    "error": "#ff4757",
    "success": "#2ed573",
    "btn_disconnect": "#c0392b",
}

STATE_LABELS = {
    TetherState.IDLE: ("Not Connected", COLORS["text_dim"]),
    TetherState.STARTING: ("Starting…", COLORS["accent_yellow"]),
    TetherState.CONNECTED: ("Connected", COLORS["accent_green"]),
    TetherState.RECONNECTING: ("Reconnecting…", COLORS["accent_yellow"]),
    TetherState.STOPPING: ("Stopping…", COLORS["accent_yellow"]),
    TetherState.ERROR: ("Error", COLORS["error"]),
}


def _pick_family(root, preferences, default="TkDefaultFont"):
    """Return the first installed font family from `preferences`, else a default."""
    available = {f.lower() for f in tkfont.families(root)}
    for fam in preferences:
        if fam.lower() in available:
            return fam
    return default


class StatCard(tk.Frame):
    """Small card showing one statistic (title over value)."""

    def __init__(self, parent, title, ui, value="—"):
        super().__init__(parent, bg=COLORS["bg_card"],
                         highlightbackground=COLORS["border"], highlightthickness=1)
        tk.Label(self, text=title, bg=COLORS["bg_card"],
                 fg=COLORS["text_secondary"], font=(ui.font, 9),
                 anchor="w").pack(fill="x", padx=10, pady=(7, 0))
        self._value = tk.Label(self, text=value, bg=COLORS["bg_card"],
                               fg=COLORS["text_primary"], font=(ui.font_bold, 14),
                               anchor="w")
        self._value.pack(fill="x", padx=10, pady=(0, 8))

    def set_value(self, value, color=None):
        # color=None resets to the default so a card doesn't keep a stale accent
        # (e.g. the Reconn card staying yellow into a fresh session).
        self._value.config(text=value, fg=color or COLORS["text_primary"])


class QuestTetherApp(tk.Tk):
    APP_TITLE = "Quest Tether"
    WIDTH = 440
    HEIGHT = 600

    def __init__(self):
        super().__init__(className="quest-tether")

        # Font families that actually exist on this host (DejaVu ships with SteamOS).
        self.font = _pick_family(self, ["Noto Sans", "DejaVu Sans", "Segoe UI", "Cantarell"])
        self.font_bold = self.font  # weight applied per-widget
        self.mono = _pick_family(self, ["DejaVu Sans Mono", "Noto Sans Mono", "Consolas", "monospace"],
                                 default="TkFixedFont")

        self.title(self.APP_TITLE)
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.minsize(400, 520)
        self.configure(bg=COLORS["bg_dark"])

        icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "assets", "icon.png")
        if os.path.isfile(icon_path):
            try:
                self._icon = tk.PhotoImage(file=icon_path)
                self.iconphoto(True, self._icon)
            except Exception:
                pass

        # --- Engine ---
        self.adb = ADBManager()
        self.tether = TetherManager(self.adb)
        self.tether.set_callbacks(
            on_state_change=self._on_state_change,
            on_stats_update=self._on_stats_update,
            on_log=self._on_log,
        )

        self._connected = False
        self._quitting = False
        self._device_detected = False
        self._auto_connect_enabled = True
        self._auto_connect_pending = False
        self._refresh_id = None

        self._build_ui()

        self.adb.start_monitoring(
            on_connected=self._on_device_connected,
            on_disconnected=self._on_device_disconnected,
        )
        self.after(400, self._initial_check)
        self._refresh_id = self.after(1000, self._refresh_loop)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # --- UI construction ---

    def _build_ui(self):
        pad = 14

        header = tk.Frame(self, bg=COLORS["bg_dark"])
        header.pack(fill="x", padx=pad, pady=(12, 0))
        tk.Label(header, text="Quest Tether", bg=COLORS["bg_dark"],
                 fg=COLORS["text_primary"], font=(self.font_bold, 17, "bold")).pack(side="left")
        self._adb_status = tk.Label(header, text="ADB: …", bg=COLORS["bg_dark"],
                                    fg=COLORS["text_dim"], font=(self.font, 9))
        self._adb_status.pack(side="right")

        # Status + toggles
        status_row = tk.Frame(self, bg=COLORS["bg_dark"])
        status_row.pack(fill="x", padx=pad, pady=(10, 0))
        dotwrap = tk.Frame(status_row, bg=COLORS["bg_dark"])
        dotwrap.pack(side="left")
        self._dot = tk.Label(dotwrap, text="●", bg=COLORS["bg_dark"],
                             fg=COLORS["text_dim"], font=(self.font, 15))
        self._dot.pack(side="left", padx=(0, 6))
        self._status_label = tk.Label(dotwrap, text="Not Connected", bg=COLORS["bg_dark"],
                                     fg=COLORS["text_dim"], font=(self.font_bold, 12, "bold"))
        self._status_label.pack(side="left")

        toggles = tk.Frame(status_row, bg=COLORS["bg_dark"])
        toggles.pack(side="right")
        self._auto_connect_var = tk.BooleanVar(value=True)
        self._auto_reconnect_var = tk.BooleanVar(value=True)
        self._make_toggle(toggles, "Reconnect", self._auto_reconnect_var,
                          self._toggle_auto_reconnect).pack(side="right", padx=(8, 0))
        self._make_toggle(toggles, "Auto", self._auto_connect_var,
                          self._toggle_auto_connect).pack(side="right")

        # Connect button
        btnwrap = tk.Frame(self, bg=COLORS["bg_dark"])
        btnwrap.pack(fill="x", padx=pad, pady=(12, 0))
        self._connect_btn = tk.Button(
            btnwrap, text="Connect", command=self._toggle_connection,
            bg=COLORS["accent"], fg=COLORS["text_primary"],
            activebackground="#c73e52", activeforeground=COLORS["text_primary"],
            font=(self.font_bold, 13, "bold"), relief="flat", bd=0,
            height=2, cursor="hand2",
        )
        self._connect_btn.pack(fill="x")
        self._device_hint = tk.Label(btnwrap, text="Plug in your Quest via USB",
                                     bg=COLORS["bg_dark"], fg=COLORS["text_dim"],
                                     font=(self.font, 9))
        self._device_hint.pack(pady=(5, 0))

        # Stats grid
        grid = tk.Frame(self, bg=COLORS["bg_dark"])
        grid.pack(fill="x", padx=pad, pady=(12, 0))
        for i in range(4):
            grid.columnconfigure(i, weight=1, uniform="stat")
        self._stat_uptime = StatCard(grid, "Uptime", self)
        self._stat_down = StatCard(grid, "Down", self)
        self._stat_up = StatCard(grid, "Up", self)
        self._stat_reconn = StatCard(grid, "Reconn", self)
        for i, c in enumerate((self._stat_uptime, self._stat_down, self._stat_up, self._stat_reconn)):
            c.grid(row=0, column=i, sticky="nsew",
                   padx=(0 if i == 0 else 3, 0 if i == 3 else 3))

        totals = tk.Frame(self, bg=COLORS["bg_dark"])
        totals.pack(fill="x", padx=pad, pady=(6, 0))
        for i in range(2):
            totals.columnconfigure(i, weight=1, uniform="tp")
        self._stat_total_rx = StatCard(totals, "Total Down", self)
        self._stat_total_tx = StatCard(totals, "Total Up", self)
        self._stat_total_rx.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        self._stat_total_tx.grid(row=0, column=1, sticky="nsew", padx=(3, 0))

        # Device info
        self._device_fields = {}
        dev = tk.Frame(self, bg=COLORS["bg_card"],
                       highlightbackground=COLORS["border"], highlightthickness=1)
        dev.pack(fill="x", padx=pad, pady=(8, 0))
        tk.Label(dev, text="Device", bg=COLORS["bg_card"], fg=COLORS["text_secondary"],
                 font=(self.font_bold, 10, "bold"), anchor="w").pack(fill="x", padx=12, pady=(7, 3))
        for label in ("Model", "Serial", "Battery", "VPN IP"):
            row = tk.Frame(dev, bg=COLORS["bg_card"])
            row.pack(fill="x", padx=12, pady=1)
            tk.Label(row, text=f"{label}:", width=8, anchor="w", bg=COLORS["bg_card"],
                     fg=COLORS["text_dim"], font=(self.font, 10)).pack(side="left")
            val = tk.Label(row, text="—", anchor="w", bg=COLORS["bg_card"],
                           fg=COLORS["text_primary"], font=(self.font, 10))
            val.pack(side="left", fill="x", expand=True)
            self._device_fields[label] = val
        tk.Frame(dev, bg=COLORS["bg_card"], height=4).pack()

        # Log
        logwrap = tk.Frame(self, bg=COLORS["bg_card"],
                           highlightbackground=COLORS["border"], highlightthickness=1)
        logwrap.pack(fill="both", expand=True, padx=pad, pady=(8, 12))
        tk.Label(logwrap, text="Log", bg=COLORS["bg_card"], fg=COLORS["text_secondary"],
                 font=(self.font_bold, 10, "bold"), anchor="w").pack(fill="x", padx=12, pady=(7, 2))
        textframe = tk.Frame(logwrap, bg=COLORS["bg_dark"])
        textframe.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        scroll = tk.Scrollbar(textframe)
        scroll.pack(side="right", fill="y")
        self._log = tk.Text(textframe, height=6, bg=COLORS["bg_dark"],
                            fg=COLORS["text_secondary"], font=(self.mono, 9),
                            relief="flat", bd=0, wrap="word", state="disabled",
                            yscrollcommand=scroll.set, insertbackground=COLORS["text_primary"])
        self._log.pack(side="left", fill="both", expand=True)
        scroll.config(command=self._log.yview)
        self._log_lines = 0

    def _make_toggle(self, parent, text, var, command):
        return tk.Checkbutton(
            parent, text=text, variable=var, command=command,
            bg=COLORS["bg_dark"], fg=COLORS["text_secondary"],
            activebackground=COLORS["bg_dark"], activeforeground=COLORS["text_primary"],
            selectcolor=COLORS["bg_card_alt"], font=(self.font, 9),
            bd=0, highlightthickness=0, cursor="hand2",
        )

    # --- Logging to the panel ---

    def _append_log(self, text):
        self._log.config(state="normal")
        self._log.insert("end", f"[{time.strftime('%H:%M:%S')}] {text}\n")
        self._log_lines += 1
        if self._log_lines > 200:
            remove = self._log_lines - 200
            self._log.delete("1.0", f"{remove + 1}.0")  # +1: delete through the line boundary
            self._log_lines = 200
        self._log.see("end")
        self._log.config(state="disabled")

    # --- Initial checks ---

    def _initial_check(self):
        # These probes shell out to adb/java (up to ~5s each) — run them off the
        # Tk main thread so the freshly-shown window stays responsive.
        threading.Thread(target=self._initial_check_worker, daemon=True).start()

    def _initial_check_worker(self):
        adb_ok = self.adb.is_adb_available()
        relay_ok = self.tether.is_relay_available()
        relay_engine = self.tether.relay_engine
        quest = self.adb.find_quest()

        def _ui():
            if self._quitting:
                return
            if adb_ok:
                self._adb_status.config(text="ADB: OK", fg=COLORS["accent_green"])
                self._append_log("ADB found")
            else:
                self._adb_status.config(text="ADB: Not Found", fg=COLORS["error"])
                self._append_log("ADB not found! Check tools/ folder.")
            self._append_log(f"gnirehtet relay ready ({relay_engine})" if relay_ok
                             else "gnirehtet relay not found!")
            if quest and quest["status"] == "device":
                self._on_device_connected(quest)
        self.after(0, _ui)

    # --- Engine callbacks (background threads → marshal onto Tk main thread) ---

    def _on_device_connected(self, device_info):
        if self._quitting:
            return
        def _ui():
            if self._quitting:
                return
            self._device_detected = True
            model = device_info.get("model", "Quest")
            serial = device_info.get("serial", "")
            if device_info.get("_unauthorized"):
                self._device_hint.config(text="Unauthorized — accept the ADB prompt in the headset",
                                         fg=COLORS["accent_yellow"])
                self._append_log(f"Device {serial} UNAUTHORIZED")
                self._connect_btn.config(state="disabled")
                return
            self._device_hint.config(text=f"{model} ({serial})", fg=COLORS["accent_green"])
            self._append_log(f"Quest detected: {model} ({serial})")
            if not self._connected:
                self._connect_btn.config(state="normal")
                if self._auto_connect_enabled and not self._auto_connect_pending:
                    self._auto_connect_pending = True
                    self._append_log("Auto-connect triggered…")
                    self._connect_btn.config(state="disabled")
                    threading.Thread(target=self._do_connect, daemon=True).start()
        self.after(0, _ui)

    def _on_device_disconnected(self):
        if self._quitting:
            return
        def _ui():
            if self._quitting:
                return
            self._device_detected = False
            self._device_hint.config(text="Plug in your Quest via USB", fg=COLORS["text_dim"])
            for v in self._device_fields.values():
                v.config(text="—")
            if not self._connected:
                self._connect_btn.config(state="normal")
            self._append_log("Device disconnected")
        self.after(0, _ui)

    def _on_state_change(self, state, stats):
        if self._quitting:
            return
        def _ui():
            if self._quitting:
                return
            label, color = STATE_LABELS.get(state, ("Unknown", COLORS["text_dim"]))
            self._dot.config(fg=color)
            self._status_label.config(text=label, fg=color)
            if state == TetherState.CONNECTED:
                self._connected = True
                self._auto_connect_pending = False
                self._connect_btn.config(text="Disconnect", state="normal",
                                         bg=COLORS["btn_disconnect"], activebackground="#e74c3c")
            elif state in (TetherState.IDLE, TetherState.ERROR):
                self._connected = False
                self._auto_connect_pending = False
                self._connect_btn.config(text="Connect", state="normal",
                                         bg=COLORS["accent"], activebackground="#c73e52")
            elif state in (TetherState.STARTING, TetherState.STOPPING, TetherState.RECONNECTING):
                # Keep _connected as-is; just lock the button so the user can't
                # race a manual disconnect against an in-flight reconnect.
                self._connect_btn.config(state="disabled")
            if state == TetherState.ERROR and stats.last_error:
                self._append_log(f"ERROR: {stats.last_error}")
        self.after(0, _ui)

    def _on_stats_update(self, stats):
        if self._quitting:
            return
        def _ui():
            if self._quitting:
                return
            self._stat_uptime.set_value(format_uptime(stats.uptime_seconds))
            self._stat_down.set_value(format_rate(stats.rx_rate_bps))
            self._stat_up.set_value(format_rate(stats.tx_rate_bps))
            self._stat_reconn.set_value(str(stats.reconnect_count),
                                        COLORS["accent_yellow"] if stats.reconnect_count else None)
            self._stat_total_rx.set_value(format_bytes(stats.rx_bytes))
            self._stat_total_tx.set_value(format_bytes(stats.tx_bytes))
            self._device_fields["Model"].config(text=stats.device_model or "—")
            self._device_fields["Serial"].config(text=stats.device_serial or "—")
            batt = stats.battery_level or "—"
            if stats.battery_status and stats.battery_status != "Unknown":
                batt = f"{batt} ({stats.battery_status})"
            self._device_fields["Battery"].config(text=batt)
            self._device_fields["VPN IP"].config(text=stats.vpn_ip or "—")
        self.after(0, _ui)

    def _on_log(self, msg):
        if self._quitting:
            return
        self.after(0, lambda: self._append_log(msg))

    # --- Actions ---

    def _toggle_connection(self):
        self._connect_btn.config(state="disabled")
        target = self._do_disconnect if self._connected else self._do_connect
        threading.Thread(target=target, daemon=True).start()

    def _do_connect(self):
        if self._quitting:
            return
        quest = self.adb.find_quest()
        if not quest:
            self.after(0, self._reset_connect_ui, "No Quest found. Plug in via USB.")
            return
        if quest["status"] != "device":
            self.after(0, self._reset_connect_ui,
                       f"Device status '{quest['status']}'. Check ADB auth on the headset.")
            return
        self.tether.connect(quest["serial"])

    def _reset_connect_ui(self, msg):
        """Recover UI state when a connect attempt bails before reaching the engine
        (device dropped between detection and this thread). Clears the auto-connect
        latch so auto-connect can fire again on the next plug-in."""
        if self._quitting:
            return
        self._append_log(msg)
        self._auto_connect_pending = False
        if not self._connected:
            self._connect_btn.config(state="normal")

    def _do_disconnect(self):
        self.tether.disconnect()

    def _toggle_auto_connect(self):
        self._auto_connect_enabled = self._auto_connect_var.get()
        self._append_log(f"Auto-connect {'on' if self._auto_connect_enabled else 'off'}")

    def _toggle_auto_reconnect(self):
        self.tether.auto_reconnect = self._auto_reconnect_var.get()
        self._append_log(f"Auto-reconnect {'on' if self._auto_reconnect_var.get() else 'off'}")

    def _refresh_loop(self):
        if self._quitting:
            return
        try:
            st = self.tether._start_time  # snapshot: a bg disconnect can null it
            if self._connected and st:
                self._stat_uptime.set_value(format_uptime(time.time() - st))
            # Re-check ADB roughly every 10s, off the UI thread.
            cyc = getattr(self, "_adb_cyc", 0) + 1
            self._adb_cyc = cyc
            if cyc >= 10:
                self._adb_cyc = 0
                threading.Thread(target=self._probe_adb_status, daemon=True).start()
        finally:
            # Always reschedule, so a transient error can't kill the refresh loop.
            if not self._quitting:
                self._refresh_id = self.after(1000, self._refresh_loop)

    def _probe_adb_status(self):
        ok = self.adb.is_adb_available()

        def _ui():
            if self._quitting:
                return
            if ok:
                self._adb_status.config(text="ADB: OK", fg=COLORS["accent_green"])
            else:
                self._adb_status.config(text="ADB: Not Found", fg=COLORS["error"])
        self.after(0, _ui)

    # --- Shutdown ---

    def _on_close(self):
        if self._quitting:
            return
        self._quitting = True
        if self._refresh_id:
            self.after_cancel(self._refresh_id)
            self._refresh_id = None
        # Prevent zombie auto-connect / reconnect during teardown.
        self._auto_connect_enabled = False
        self.tether.auto_reconnect = False
        # "Engine active" = the relay is up OR coming up. _running is set the
        # instant connect() begins (STARTING) and stays set through CONNECTED /
        # RECONNECTING — so this catches a close mid-STARTING, where _connected is
        # still False but the Quest's VPN is already up and needs a graceful
        # disconnect() to restore Wi-Fi/DNS. force_kill (idle case) does not.
        active = bool(self.tether._running or self._connected)
        if active:
            self._dot.config(fg=COLORS["accent_yellow"])
            self._status_label.config(text="Disconnecting… restoring Wi-Fi",
                                     fg=COLORS["accent_yellow"])
            self._connect_btn.config(state="disabled")
            self.update_idletasks()  # render the feedback before the blocking teardown
        threading.Thread(target=self._shutdown_and_exit, args=(active,), daemon=True).start()

    def _shutdown_and_exit(self, active):
        # Watchdog: guarantee the process dies within a bounded time even if an
        # adb call in disconnect() wedges (e.g. the Quest was unplugged mid-session).
        threading.Timer(30.0, lambda: os._exit(0)).start()
        self.tether._running = False
        try:
            self.adb.stop_monitoring()   # joins the poll thread — off the UI thread now
        except Exception:
            pass
        try:
            if active:
                self.tether.disconnect()   # restores the Quest's Wi-Fi/DNS
            else:
                self.tether.force_kill()
        except Exception:
            pass
        # Hard exit so no daemon thread keeps the process alive.
        os._exit(0)


def main():
    app = QuestTetherApp()
    # Self-test: render without auto-tethering, then exit cleanly. Used to verify
    # the GUI builds/starts on a host without disturbing the Quest.
    if os.environ.get("QUESTTETHER_SELFTEST"):
        app._auto_connect_enabled = False
        try:
            app._auto_connect_var.set(False)
        except Exception:
            pass
        app.after(2500, lambda: os._exit(0))
    app.mainloop()
    return 0


if __name__ == "__main__":
    main()
