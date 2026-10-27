# Resolved questions

Technical questions answered during development, kept for future debugging.

## Quest 2 hardware and ADB

- Model string from `ro.product.model` is `Quest_2`. Detection works on first plug.
- Shell tools on the Quest 2: `toybox nc` exists and works for TCP connectivity checks. `wget` and `nslookup` are not available. `ping` works. The primary connectivity check therefore uses `toybox nc`.
- Wi-Fi at test time: WPA2-PSK on 2.4 GHz and 5 GHz, RSSI about -43 dBm, link speed 192 Mbps.

## Relay and networking

- Rust vs Java relay: the Rust relay starts but no traffic flows on Windows; the Java relay works immediately. Likely Windows Firewall or socket binding, root cause not confirmed (issue #9). I decided Java is permanent.
- Cold start: with all the patched code, the connection comes up with no manual ADB intervention: relay starts, the `localabstract` tunnel is set up, the explicit-component intent fires, the VPN comes up, `tun0` gets an IP, traffic flows.

## Windows and Python

- CustomTkinter renders cleanly on Windows 11 in the dark theme, with no artifacts or lag.
- Tested with customtkinter 5.2.2 and Python 3.11.
- ADB is not on the system PATH; it is bundled in `tools/` and the app finds it automatically.
- Tested with JDK 25 on PATH. The relay runs as `java -jar gnirehtet.jar relay`.
- Windows Firewall prompted for ADB on first connection and I allowed it. The Java relay binds to localhost only (127.0.0.1:31416), so no inbound rule is needed.
