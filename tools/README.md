# tools/

These are the third-party helpers the app expects at a fixed path next to the program, supplied by the package from the launcher, never committed.

- Windows: `adb.exe`, `AdbWinApi.dll`, `AdbWinUsbApi.dll` from "SDK Platform-Tools for Windows" (https://developer.android.com/tools/releases/platform-tools); `gnirehtet.jar` and `gnirehtet.apk` from the Java release of gnirehtet (https://github.com/Genymobile/gnirehtet/releases, the zip `gnirehtet-java-*.zip`), not the Rust one (no traffic on Windows, #9); a Java runtime on PATH.
  Version used: platform-tools 36.0.2 (AdbWinApi.dll 2.0.0.0, AdbWinUsbApi.dll 2.0.0.1); gnirehtet 2.5.1
- Linux: `adb` from "SDK Platform-Tools for Linux" (same page); `gnirehtet` (native binary) and `gnirehtet.apk` from the Rust release (`gnirehtet-rust-linux64-*.zip`); no Java needed.
  Version used: platform-tools 37.0.0 (adb 1.0.41); gnirehtet 2.5.1
