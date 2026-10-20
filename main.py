"""
Quest Tether — Windows native GUI for gnirehtet reverse tethering.
No headset reboot required. Auto-detect, auto-reconnect, live stats.
"""

import sys
import os
import threading
import time
import logging
import atexit
from datetime import timedelta

# Ensure imports work when running from different directories
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import customtkinter as ctk
from adb_manager import ADBManager
from tether_manager import TetherManager, TetherState, TetherStats

# System tray — graceful degradation if not installed
try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# --- Theme ---
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Color palette
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
}


def format_bytes(b: int) -> str:
    """Format byte count to human-readable."""
    if b < 1024:
        return f"{b} B"
    elif b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 ** 3:
        return f"{b / (1024 ** 2):.1f} MB"
    else:
        return f"{b / (1024 ** 3):.2f} GB"


def format_rate(bps: float) -> str:
    """Format bytes/sec to human-readable rate."""
    if bps < 1024:
        return f"{bps:.0f} B/s"
    elif bps < 1024 ** 2:
        return f"{bps / 1024:.1f} KB/s"
    else:
        return f"{bps / (1024 ** 2):.1f} MB/s"


def format_uptime(seconds: float) -> str:
    """Format uptime seconds to HH:MM:SS."""
    td = timedelta(seconds=int(seconds))
    hours, remainder = divmod(td.seconds + td.days * 86400, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


STATE_LABELS = {
    TetherState.IDLE: ("Not Connected", COLORS["text_dim"]),
    TetherState.STARTING: ("Starting...", COLORS["accent_yellow"]),
    TetherState.CONNECTED: ("Connected", COLORS["accent_green"]),
    TetherState.RECONNECTING: ("Reconnecting...", COLORS["accent_yellow"]),
    TetherState.STOPPING: ("Stopping...", COLORS["accent_yellow"]),
    TetherState.ERROR: ("Error", COLORS["error"]),
}


class StatusIndicator(ctk.CTkFrame):
    """Connection status indicator (colored dot + label)."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)

        self._dot = ctk.CTkLabel(
            self, text="\u25cf", font=ctk.CTkFont(size=18),
            text_color=COLORS["text_dim"], width=24,
        )
        self._dot.pack(side="left", padx=(0, 6))

        self._label = ctk.CTkLabel(
            self, text="Not Connected",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color=COLORS["text_dim"],
        )
        self._label.pack(side="left")

    def set_state(self, state: TetherState):
        label_text, color = STATE_LABELS.get(state, ("Unknown", COLORS["text_dim"]))
        self._dot.configure(text_color=color)
        self._label.configure(text=label_text, text_color=color)


class StatCard(ctk.CTkFrame):
    """Small card showing a single statistic."""

    def __init__(self, parent, title: str, value: str = "\u2014", **kwargs):
        super().__init__(
            parent, fg_color=COLORS["bg_card"], corner_radius=8,
            border_width=1, border_color=COLORS["border"], **kwargs
        )

        self._title = ctk.CTkLabel(
            self, text=title,
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=COLORS["text_secondary"],
        )
        self._title.pack(padx=10, pady=(8, 1), anchor="w")

        self._value = ctk.CTkLabel(
            self, text=value,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self._value.pack(padx=10, pady=(0, 8), anchor="w")

    def set_value(self, value: str):
        self._value.configure(text=value)

    def set_color(self, color: str):
        self._value.configure(text_color=color)


class DeviceInfoPanel(ctk.CTkFrame):
    """Panel showing connected device information."""

    def __init__(self, parent, **kwargs):
        super().__init__(
            parent, fg_color=COLORS["bg_card"], corner_radius=8,
            border_width=1, border_color=COLORS["border"], **kwargs
        )

        header = ctk.CTkLabel(
            self, text="Device",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=COLORS["text_secondary"],
        )
        header.pack(padx=12, pady=(8, 4), anchor="w")

        self._info_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._info_frame.pack(fill="x", padx=12, pady=(0, 8))

        self._fields = {}
        for label in ["Model", "Serial", "Battery", "VPN IP"]:
            row = ctk.CTkFrame(self._info_frame, fg_color="transparent")
            row.pack(fill="x", pady=1)

            lbl = ctk.CTkLabel(
                row, text=f"{label}:", width=55, anchor="w",
                font=ctk.CTkFont(family="Segoe UI", size=11),
                text_color=COLORS["text_dim"],
            )
            lbl.pack(side="left")

            val = ctk.CTkLabel(
                row, text="\u2014", anchor="w",
                font=ctk.CTkFont(family="Segoe UI", size=11),
                text_color=COLORS["text_primary"],
            )
            val.pack(side="left", fill="x", expand=True)
            self._fields[label] = val

    def update_info(self, stats: TetherStats):
        self._fields["Model"].configure(text=stats.device_model or "\u2014")
        self._fields["Serial"].configure(text=stats.device_serial or "\u2014")
        batt = stats.battery_level or "\u2014"
        if stats.battery_status and stats.battery_status != "Unknown":
            batt = f"{batt} ({stats.battery_status})"
        self._fields["Battery"].configure(text=batt)
        self._fields["VPN IP"].configure(text=stats.vpn_ip or "\u2014")

    def clear(self):
        for val in self._fields.values():
            val.configure(text="\u2014")


class LogPanel(ctk.CTkFrame):
    """Scrollable log output panel."""

    MAX_LINES = 200

    def __init__(self, parent, **kwargs):
        super().__init__(
            parent, fg_color=COLORS["bg_card"], corner_radius=8,
            border_width=1, border_color=COLORS["border"], **kwargs
        )

        header = ctk.CTkLabel(
            self, text="Log",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=COLORS["text_secondary"],
        )
        header.pack(padx=12, pady=(8, 2), anchor="w")

        self._textbox = ctk.CTkTextbox(
            self, font=ctk.CTkFont(family="Consolas", size=10),
            fg_color=COLORS["bg_dark"], text_color=COLORS["text_secondary"],
            corner_radius=6, height=100, state="disabled",
            border_width=0,
        )
        self._textbox.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._line_count = 0

    def append(self, text: str):
        self._textbox.configure(state="normal")
        timestamp = time.strftime("%H:%M:%S")
        self._textbox.insert("end", f"[{timestamp}] {text}\n")
        self._line_count += 1

        if self._line_count > self.MAX_LINES:
            self._textbox.delete("1.0", f"{self._line_count - self.MAX_LINES}.0")
            self._line_count = self.MAX_LINES

        self._textbox.see("end")
        self._textbox.configure(state="disabled")

    def clear(self):
        self._textbox.configure(state="normal")
        self._textbox.delete("1.0", "end")
        self._textbox.configure(state="disabled")
        self._line_count = 0


class QuestTetherApp(ctk.CTk):
    """Main application window."""

    APP_TITLE = "Quest Tether"
    WINDOW_WIDTH = 420
    WINDOW_HEIGHT = 560

    def __init__(self, start_minimized: bool = False):
        super().__init__()

        self._start_minimized = start_minimized

        self.title(self.APP_TITLE)
        self.geometry(f"{self.WINDOW_WIDTH}x{self.WINDOW_HEIGHT}")
        self.minsize(360, 480)
        self.configure(fg_color=COLORS["bg_dark"])

        # Set window icon if available
        icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon.ico")
        if os.path.isfile(icon_path):
            self.iconbitmap(icon_path)

        # --- Core Managers ---
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

        # --- Build UI ---
        self._build_ui()

        # --- Start device monitoring ---
        self.adb.start_monitoring(
            on_connected=self._on_device_connected,
            on_disconnected=self._on_device_disconnected,
        )

        # Initial state check
        self.after(500, self._initial_check)

        # Periodic UI refresh
        self._ui_refresh_id = self.after(1000, self._ui_refresh_loop)

        # System tray
        self._tray_icon = None
        if HAS_TRAY:
            self._setup_tray()

        # Start minimized to tray (e.g. on boot via --minimized flag)
        if self._start_minimized and HAS_TRAY:
            self.withdraw()

        # Window close → minimize to tray (or quit if no tray)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Minimize button → also go to tray
        self.bind("<Unmap>", self._on_minimize)

    def _build_ui(self):
        """Construct the main interface."""
        pad = 16  # Outer padding

        # --- Header ---
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(fill="x", padx=pad, pady=(14, 0))

        title_label = ctk.CTkLabel(
            header_frame, text="Quest Tether",
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title_label.pack(side="left")

        # ADB status indicator (top right)
        self._adb_status = ctk.CTkLabel(
            header_frame, text="ADB: ...",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=COLORS["text_dim"],
        )
        self._adb_status.pack(side="right")

        # --- Status Row ---
        status_frame = ctk.CTkFrame(self, fg_color="transparent")
        status_frame.pack(fill="x", padx=pad, pady=(10, 0))

        self._status_indicator = StatusIndicator(status_frame)
        self._status_indicator.pack(side="left")

        # Toggle switches (right side)
        switches_frame = ctk.CTkFrame(status_frame, fg_color="transparent")
        switches_frame.pack(side="right")

        self._auto_connect_var = ctk.BooleanVar(value=True)
        self._auto_connect_switch = ctk.CTkSwitch(
            switches_frame, text="Auto",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=COLORS["text_secondary"],
            variable=self._auto_connect_var,
            command=self._toggle_auto_connect,
            width=36, height=18,
        )
        self._auto_connect_switch.pack(side="right", padx=(8, 0))

        self._auto_reconnect_var = ctk.BooleanVar(value=True)
        self._auto_reconnect_switch = ctk.CTkSwitch(
            switches_frame, text="Reconnect",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=COLORS["text_secondary"],
            variable=self._auto_reconnect_var,
            command=self._toggle_auto_reconnect,
            width=36, height=18,
        )
        self._auto_reconnect_switch.pack(side="right")

        # --- Connect / Disconnect Button ---
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=pad, pady=(10, 0))

        self._connect_btn = ctk.CTkButton(
            btn_frame, text="Connect", height=38,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=14),
            fg_color=COLORS["accent"], hover_color="#c73e52",
            corner_radius=8, command=self._toggle_connection,
        )
        self._connect_btn.pack(fill="x")

        # Device detection hint
        self._device_hint = ctk.CTkLabel(
            btn_frame, text="Plug in your Quest via USB",
            font=ctk.CTkFont(family="Segoe UI", size=10),
            text_color=COLORS["text_dim"],
        )
        self._device_hint.pack(pady=(4, 0))

        # --- Stats Grid ---
        stats_frame = ctk.CTkFrame(self, fg_color="transparent")
        stats_frame.pack(fill="x", padx=pad, pady=(10, 0))
        stats_frame.columnconfigure((0, 1, 2, 3), weight=1, uniform="stat")

        self._stat_uptime = StatCard(stats_frame, "Uptime")
        self._stat_uptime.grid(row=0, column=0, padx=(0, 3), sticky="nsew")

        self._stat_download = StatCard(stats_frame, "Down")
        self._stat_download.grid(row=0, column=1, padx=3, sticky="nsew")

        self._stat_upload = StatCard(stats_frame, "Up")
        self._stat_upload.grid(row=0, column=2, padx=3, sticky="nsew")

        self._stat_reconnects = StatCard(stats_frame, "Reconn")
        self._stat_reconnects.grid(row=0, column=3, padx=(3, 0), sticky="nsew")

        # --- Throughput Row ---
        throughput_frame = ctk.CTkFrame(self, fg_color="transparent")
        throughput_frame.pack(fill="x", padx=pad, pady=(6, 0))
        throughput_frame.columnconfigure((0, 1), weight=1, uniform="tp")

        self._stat_total_rx = StatCard(throughput_frame, "Total Down")
        self._stat_total_rx.grid(row=0, column=0, padx=(0, 3), sticky="nsew")

        self._stat_total_tx = StatCard(throughput_frame, "Total Up")
        self._stat_total_tx.grid(row=0, column=1, padx=(3, 0), sticky="nsew")

        # --- Device Info ---
        self._device_panel = DeviceInfoPanel(self)
        self._device_panel.pack(fill="x", padx=pad, pady=(8, 0))

        # --- Log ---
        self._log_panel = LogPanel(self)
        self._log_panel.pack(fill="both", expand=True, padx=pad, pady=(8, 14))

    # --- Initial Checks ---

    def _initial_check(self):
        """Run initial system checks."""
        if self.adb.is_adb_available():
            self._adb_status.configure(text="ADB: OK", text_color=COLORS["accent_green"])
            self._log_panel.append(f"ADB found")
        else:
            self._adb_status.configure(text="ADB: Not Found", text_color=COLORS["error"])
            self._log_panel.append("ADB not found! Place adb.exe in tools/ folder.")

        if self.tether.is_relay_available():
            self._log_panel.append(f"Gnirehtet relay ready")
        else:
            self._log_panel.append("Gnirehtet relay not found!")

        quest = self.adb.find_quest()
        if quest and quest["status"] == "device":
            self._on_device_connected(quest)

    # --- Callbacks (called from background threads — must schedule UI updates) ---

    def _on_device_connected(self, device_info: dict):
        """Called when a Quest is detected on USB."""
        if self._quitting:
            return
        def _ui():
            if self._quitting:
                return
            self._device_detected = True
            is_unauth = device_info.get("_unauthorized", False)
            model = device_info.get("model", "Quest")
            serial = device_info.get("serial", "")

            if is_unauth:
                self._device_hint.configure(
                    text="Unauthorized \u2014 check headset for ADB prompt",
                    text_color=COLORS["accent_yellow"],
                )
                self._log_panel.append(f"Device {serial} UNAUTHORIZED")
                self._connect_btn.configure(state="disabled")
            else:
                self._device_hint.configure(
                    text=f"{model} ({serial})",
                    text_color=COLORS["accent_green"],
                )
                self._log_panel.append(f"Quest detected: {model} ({serial})")
                if not self._connected:
                    self._connect_btn.configure(state="normal")
                    if self._auto_connect_enabled and not self._auto_connect_pending:
                        self._auto_connect_pending = True
                        self._log_panel.append("Auto-connect triggered...")
                        self._connect_btn.configure(state="disabled")
                        threading.Thread(target=self._do_connect, daemon=True).start()
        self.after(0, _ui)

    def _on_device_disconnected(self):
        """Called when the Quest is unplugged."""
        if self._quitting:
            return
        def _ui():
            if self._quitting:
                return
            self._device_detected = False
            self._device_hint.configure(
                text="Plug in your Quest via USB",
                text_color=COLORS["text_dim"],
            )
            self._device_panel.clear()
            if not self._connected:
                self._connect_btn.configure(state="normal")
            self._log_panel.append("Device disconnected")
        self.after(0, _ui)

    def _on_state_change(self, state: TetherState, stats: TetherStats):
        """Called when tether state changes."""
        if self._quitting:
            return
        def _ui():
            if self._quitting:
                return
            self._status_indicator.set_state(state)

            if state == TetherState.CONNECTED:
                self._connected = True
                self._auto_connect_pending = False
                self._connect_btn.configure(
                    text="Disconnect", height=44,
                    font=ctk.CTkFont(family="Segoe UI Semibold", size=15, weight="bold"),
                    fg_color="#c0392b", hover_color="#e74c3c",
                    state="normal",
                )
            elif state in (TetherState.IDLE, TetherState.ERROR):
                self._connected = False
                self._auto_connect_pending = False
                self._connect_btn.configure(
                    text="Connect", height=38,
                    font=ctk.CTkFont(family="Segoe UI Semibold", size=14),
                    fg_color=COLORS["accent"], hover_color="#c73e52",
                    state="normal",
                )
            elif state in (TetherState.STARTING, TetherState.STOPPING):
                self._connect_btn.configure(state="disabled")

            if state == TetherState.ERROR and stats.last_error:
                self._log_panel.append(f"ERROR: {stats.last_error}")

            # Update tray icon color
            if HAS_TRAY and self._tray_icon:
                self._tray_icon.icon = self._create_tray_image(
                    state == TetherState.CONNECTED
                )
        self.after(0, _ui)

    def _on_stats_update(self, stats: TetherStats):
        """Called periodically with updated stats."""
        if self._quitting:
            return
        def _ui():
            if self._quitting:
                return
            self._stat_uptime.set_value(format_uptime(stats.uptime_seconds))
            self._stat_download.set_value(format_rate(stats.rx_rate_bps))
            self._stat_upload.set_value(format_rate(stats.tx_rate_bps))
            self._stat_reconnects.set_value(str(stats.reconnect_count))
            self._stat_total_rx.set_value(format_bytes(stats.rx_bytes))
            self._stat_total_tx.set_value(format_bytes(stats.tx_bytes))
            self._device_panel.update_info(stats)

            if stats.reconnect_count > 0:
                self._stat_reconnects.set_color(COLORS["accent_yellow"])
        self.after(0, _ui)

    def _on_log(self, msg: str):
        """Called from tether manager with log messages."""
        if self._quitting:
            return
        self.after(0, lambda: self._log_panel.append(msg))

    # --- UI Actions ---

    def _toggle_connection(self):
        if self._connected:
            self._connect_btn.configure(state="disabled")
            threading.Thread(target=self._do_disconnect, daemon=True).start()
        else:
            self._connect_btn.configure(state="disabled")
            threading.Thread(target=self._do_connect, daemon=True).start()

    def _do_connect(self):
        if self._quitting:
            return
        quest = self.adb.find_quest()
        if not quest:
            self.after(0, lambda: self._log_panel.append("No Quest found. Plug in via USB."))
            self.after(0, lambda: self._connect_btn.configure(state="normal"))
            return
        if quest["status"] != "device":
            self.after(0, lambda: self._log_panel.append(
                f"Device status: '{quest['status']}'. Check ADB auth on headset."
            ))
            self.after(0, lambda: self._connect_btn.configure(state="normal"))
            return
        self.tether.connect(quest["serial"])

    def _do_disconnect(self):
        self.tether.disconnect()

    def _toggle_auto_connect(self):
        self._auto_connect_enabled = self._auto_connect_var.get()
        state = "on" if self._auto_connect_enabled else "off"
        self._log_panel.append(f"Auto-connect {state}")

    def _toggle_auto_reconnect(self):
        self.tether.auto_reconnect = self._auto_reconnect_var.get()
        state = "on" if self._auto_reconnect_var.get() else "off"
        self._log_panel.append(f"Auto-reconnect {state}")

    def _ui_refresh_loop(self):
        """1-second UI refresh."""
        if self._quitting:
            return

        if self._connected and self.tether._start_time:
            uptime = time.time() - self.tether._start_time
            self._stat_uptime.set_value(format_uptime(uptime))

        if not hasattr(self, '_adb_check_cycle'):
            self._adb_check_cycle = 0
        self._adb_check_cycle += 1
        if self._adb_check_cycle >= 10:
            self._adb_check_cycle = 0
            if self.adb.is_adb_available():
                self._adb_status.configure(text="ADB: OK", text_color=COLORS["accent_green"])
            else:
                self._adb_status.configure(text="ADB: Not Found", text_color=COLORS["error"])

        if HAS_TRAY:
            self._update_tray()

        self._ui_refresh_id = self.after(1000, self._ui_refresh_loop)

    # --- System Tray ---

    def _create_tray_image(self, connected=False):
        """Generate tray icon — simple bold Q, green when connected."""
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Dark rounded background
        draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=14, fill="#1a1a2e")

        # Bold "Q" letter
        color = "#00d97e" if connected else "#e94560"
        try:
            font = ImageFont.truetype("segoeuib.ttf", 44)
        except (IOError, OSError):
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), "Q", font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (size - tw) // 2 - bbox[0]
        y = (size - th) // 2 - bbox[1] - 1
        draw.text((x, y), "Q", fill=color, font=font)

        return img

    def _setup_tray(self):
        """Create and start the system tray icon."""
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._tray_show, default=True),
            pystray.MenuItem("Disconnect", self._tray_disconnect),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self._tray_icon = pystray.Icon(
            "quest_tether",
            self._create_tray_image(False),
            "Quest Tether",
            menu,
        )
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _tray_show(self, icon=None, item=None):
        self.after(0, self._restore_window)

    def _restore_window(self):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.focus_force()

    def _tray_disconnect(self, icon=None, item=None):
        if self._connected:
            threading.Thread(target=self._do_disconnect, daemon=True).start()

    def _tray_quit(self, icon=None, item=None):
        self.after(0, self._quit_app)

    def _update_tray(self):
        if not self._tray_icon:
            return
        if self._connected and self.tether._start_time:
            uptime = time.time() - self.tether._start_time
            rx = format_rate(self.tether.stats.rx_rate_bps)
            tx = format_rate(self.tether.stats.tx_rate_bps)
            up = format_uptime(uptime)
            self._tray_icon.title = f"Quest Tether\n\u2193{rx} \u2191{tx} | {up}"
        else:
            self._tray_icon.title = "Quest Tether - Idle"

    # --- Window Management ---

    def _on_minimize(self, event):
        """Intercept minimize button — go to tray instead of taskbar."""
        if HAS_TRAY and self._tray_icon and self.state() == "iconic":
            self.withdraw()

    def _on_close(self):
        """X button → minimize to tray. Quit if no tray."""
        if HAS_TRAY and self._tray_icon:
            self.withdraw()
        else:
            self._quit_app()

    def _quit_app(self):
        """Full shutdown — prevent zombie restarts."""
        if self._quitting:
            return
        self._quitting = True
        logger.info("Shutting down...")

        # Disable auto-connect FIRST — prevents zombie restart
        self._auto_connect_enabled = False
        self.tether._running = False
        self.tether.auto_reconnect = False

        # Stop monitoring BEFORE killing process — prevents race condition
        # where monitor sees device after kill and triggers auto-connect
        self.adb.stop_monitoring()

        # Cancel UI refresh
        if self._ui_refresh_id:
            self.after_cancel(self._ui_refresh_id)
            self._ui_refresh_id = None

        # If connected, clean up the Quest before killing Java
        serial = self.tether.stats.device_serial
        if self._connected and serial:
            self.adb.stop_gnirehtet_client(serial)
            self.adb.force_stop_gnirehtet(serial)
            self.adb.toggle_wifi(serial, wait_seconds=2.0)

        # Kill tracked Java process
        self.tether.force_kill()

        # Kill ANY untracked gnirehtet on port 31416 (race condition safety net)
        self.tether._kill_stale_process()

        # Stop tray icon
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass

        self.destroy()

        # Hard exit — prevents any lingering threads from keeping Python alive
        os._exit(0)


# Global reference for atexit cleanup
_app_instance = None


def _atexit_cleanup():
    """Safety net: kill relay process if app dies unexpectedly."""
    if _app_instance and _app_instance.tether._process:
        try:
            _app_instance.tether.force_kill()
        except Exception:
            pass


def main():
    global _app_instance
    start_minimized = "--minimized" in sys.argv
    app = QuestTetherApp(start_minimized=start_minimized)
    _app_instance = app
    atexit.register(_atexit_cleanup)
    app.mainloop()


if __name__ == "__main__":
    main()
