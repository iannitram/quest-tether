"""
Platform abstraction for Quest Tether.

Centralizes OS detection, subprocess console-window suppression, process-group
handling, and discovery of the bundled ADB / gnirehtet binaries so the rest of
the app stays platform-agnostic. The original Windows app scattered
`subprocess.CREATE_NO_WINDOW` and `os.name == "nt"` checks throughout; they all
live here now.
"""

import os
import sys
import shutil
import signal
import subprocess
import time

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# Suppress console windows for subprocess calls on Windows; no-op elsewhere.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0

# Extra Popen kwargs to launch a child in its own process group / session, so we
# can signal the whole tree later (gnirehtet spawns adb helpers). On POSIX this
# also means SIGTERM reaches gnirehtet, which then stops its VPN client cleanly.
if IS_WINDOWS:
    _NEW_GROUP_FLAGS = NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    NEW_GROUP_KW = {"creationflags": _NEW_GROUP_FLAGS}
else:
    NEW_GROUP_KW = {"start_new_session": True}


def _base_dirs():
    """Directories that may contain a bundled ``tools/`` folder, most specific first."""
    dirs = []
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(sys.executable))
    # This file lives in <root>/questtether/; tools/ sits at <root>/tools/.
    dirs.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return dirs


def tools_dirs():
    return [os.path.join(d, "tools") for d in _base_dirs()]


def find_binary(candidates, env_var=None):
    """Locate a bundled tool.

    candidates: filenames (or tools/-relative paths) to try, in priority order.
    env_var:    optional environment variable that points directly at the binary.

    Search order: env override → tools/ dirs → PATH → bare name (so a missing
    tool fails later with a clear "not found" rather than here).
    """
    if env_var:
        override = os.environ.get(env_var)
        if override and os.path.isfile(override):
            return override
    for td in tools_dirs():
        for name in candidates:
            p = os.path.join(td, name)
            if os.path.isfile(p):
                return p
    for name in candidates:
        found = shutil.which(os.path.basename(name))
        if found:
            return found
    return os.path.basename(candidates[-1])


def run(cmd, timeout=10, **kwargs):
    """subprocess.run wrapper that captures text output and hides Windows consoles."""
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    if NO_WINDOW and "creationflags" not in kwargs:
        kwargs["creationflags"] = NO_WINDOW
    try:
        return subprocess.run(cmd, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 1, "", "Timeout")
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 1, "", f"Not found: {cmd[0]}")


def terminate_tree(proc, timeout=3.0, log=None):
    """Stop a child process launched with NEW_GROUP_KW, escalating as needed.

    POSIX: SIGTERM the process group (gnirehtet catches it and stops its VPN
    client), then SIGKILL the group if it lingers. Windows: terminate(), then
    ``taskkill /F /T`` on the tree.
    """
    if proc is None or proc.poll() is not None:
        return
    pid = proc.pid
    if IS_WINDOWS:
        try:
            proc.terminate()
            proc.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            if log:
                log("Didn't stop gracefully — killing...")
            run(["taskkill", "/F", "/T", "/PID", str(pid)], timeout=5)
        return
    # POSIX
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    if log:
        log("Didn't stop gracefully — killing...")
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def pids_on_port(port):
    """Return PIDs of processes listening on a TCP port (best effort, POSIX)."""
    if IS_WINDOWS:
        pids = set()
        result = run(["netstat", "-ano"], timeout=5)
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pids.add(int(parts[-1]))
        return pids
    pids = set()
    # ss is present on SteamOS; -H omits the header, filter by source port.
    result = run(["ss", "-tlnpH", f"sport = :{port}"], timeout=5)
    if result.returncode == 0 and result.stdout.strip():
        import re
        for m in re.finditer(r"pid=(\d+)", result.stdout):
            pids.add(int(m.group(1)))
    return pids
