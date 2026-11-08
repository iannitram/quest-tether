"""settings.json for the service's persisted settings (#117). Pure file logic, no OS calls.

The file lives in the platform config folder (Platform.config_dir). It holds
auto_reconnect and wifi_mode. crash_reports stays in crash_report's own file.
Loads never raise: a missing, unreadable or bad file gives {} and the defaults stay.
Saves are atomic (temp file, fsync, os.replace) and return False on failure.
"""

import json
import logging
import os
import tempfile
from typing import Any, Dict

VERSION = 1
FILE_NAME = "settings.json"
WIFI_MODES = ("off", "sync", "leave")
# The keys this file stores. crash_reports is not one of them.
PERSISTED_KEYS = ("auto_reconnect", "wifi_mode")

log = logging.getLogger(__name__)


def settings_path(config_dir: str) -> str:
    return os.path.join(config_dir, FILE_NAME)


def valid_value(key: str, value: Any) -> bool:
    """One rule for a setting's value, shared by the file and the settings-set request."""
    if key in ("auto_reconnect", "crash_reports"):
        return isinstance(value, bool)
    if key == "wifi_mode":
        return isinstance(value, str) and value in WIFI_MODES
    return False


def load_settings(path: str) -> Dict[str, Any]:
    """The valid persisted settings in path; {} when the file is missing, unreadable or not a dict.

    A bad value drops only its own key. Unknown keys and the version are ignored
    (a newer version still gives its known keys). Never raises.
    """
    try:
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, RecursionError):
        log.warning("settings file %s is unreadable; using the defaults", path)
        return {}
    if not isinstance(data, dict):
        log.warning("settings file %s is not a JSON object; using the defaults", path)
        return {}

    loaded: Dict[str, Any] = {}
    for key in PERSISTED_KEYS:
        if key not in data:
            continue
        if valid_value(key, data[key]):
            loaded[key] = data[key]
        else:
            log.warning("settings file %s: dropped bad %s", path, key)
    return loaded


def save_settings(path: str, settings: Dict[str, Any]) -> bool:
    """Write the valid persisted keys of settings to path atomically; True on success.

    The temp file sits in the same folder, so os.replace is atomic. On any
    OSError the temp file is removed, the old file stays, and False comes back.
    """
    payload: Dict[str, Any] = {"version": VERSION}
    for key in PERSISTED_KEYS:
        if key in settings and valid_value(key, settings[key]):
            payload[key] = settings[key]

    directory = os.path.dirname(path) or "."
    temp = None
    try:
        os.makedirs(directory, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=directory, prefix=".settings-", suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        return True
    except OSError as exc:
        log.warning("could not save settings to %s: %s", path, exc)
        if temp is not None:
            try:
                os.unlink(temp)
            except OSError:
                pass
        return False
