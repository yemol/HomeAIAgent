#!/usr/bin/env python3
import argparse
import getpass
import json
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    raise SystemExit(
        "pyserial missing. Install once with:\n"
        "  python3 -m pip install pyserial"
    )

def choose_port(explicit):
    if explicit:
        return explicit
    ports = list(list_ports.comports())
    likely = [
        p for p in ports
        if "usbmodem" in p.device.lower()
        or "usbserial" in p.device.lower()
        or "esp" in (p.description or "").lower()
    ]
    candidates = likely or ports
    if not candidates:
        raise SystemExit("No serial port found.")
    if len(candidates) == 1:
        return candidates[0].device
    print("Available serial ports:")
    for idx, p in enumerate(candidates, 1):
        print(f"  {idx}. {p.device}  {p.description}")
    n = int(input("Select: ").strip())
    return candidates[n - 1].device

parser = argparse.ArgumentParser()
parser.add_argument("--port")
parser.add_argument("--show", action="store_true")
parser.add_argument("--reset", action="store_true")
args = parser.parse_args()

port = choose_port(args.port)
print(f"[SERIAL] {port}")

with serial.Serial(port, 115200, timeout=0.25) as ser:
    time.sleep(0.7)
    ser.reset_input_buffer()

    if args.show:
        ser.write(b"CFG SHOW\n")
    elif args.reset:
        confirm = input("Type RESET to factory-reset terminal NVS: ")
        if confirm != "RESET":
            raise SystemExit("Cancelled.")
        ser.write(b"CFG RESET\n")
    else:
        ssid = input("Wi-Fi SSID: ")
        password = getpass.getpass("Wi-Fi password: ")
        host = input("Mac mini LAN IP / hostname: ")
        port_num = int(input("Gateway port [8765]: ") or "8765")
        path = input("Gateway path [/companion]: ") or "/companion"
        name = input("Device name [HomeAIAgent]: ") or "HomeAIAgent"
        payload = {
            "wifi_ssid": ssid,
            "wifi_password": password,
            "gateway_host": host,
            "gateway_port": port_num,
            "gateway_path": path,
            "device_name": name,
        }
        line = "CFG " + json.dumps(payload, ensure_ascii=False) + "\n"
        ser.write(line.encode("utf-8"))

    deadline = time.time() + 5
    while time.time() < deadline:
        raw = ser.readline()
        if raw:
            print(raw.decode("utf-8", errors="replace").rstrip())
