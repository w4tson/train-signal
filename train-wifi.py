#!/usr/bin/env python3

import csv
import os
import re
import subprocess
import time
from datetime import datetime


# ============================================================
# CONFIGURATION
# ============================================================

HOTSPOT_NAME = ""
HOTSPOT_PASSWORD = ""

# Leave as None to auto-detect the Mac's Wi-Fi interface.
WIFI_DEVICE = None

CHECK_INTERVAL_SECONDS = 5
SPEED_TEST_INTERVAL_SECONDS = 30

# 2 MB download for each speed sample.
TEST_URL = "https://speed.cloudflare.com/__down?bytes=2000000"

DOWNLOAD_TIMEOUT_SECONDS = 15

OUTPUT_FILE = "train_network01.csv"


# ============================================================
# GENERAL HELPERS
# ============================================================

def timestamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def run_command(command, timeout=20):
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        return (
            result.returncode,
            result.stdout.strip(),
            result.stderr.strip()
        )

    except subprocess.TimeoutExpired:
        return 999, "", "command timed out"

    except Exception as exc:
        return 998, "", str(exc)


# ============================================================
# WI-FI / INTERFACE DETECTION
# ============================================================

def find_wifi_device():
    """
    Find the BSD interface corresponding to Wi-Fi.

    Usually en0, but we ask macOS instead of assuming.
    """

    code, stdout, stderr = run_command([
        "/usr/sbin/networksetup",
        "-listallhardwareports"
    ])

    if code != 0:
        raise RuntimeError(
            f"Could not inspect network hardware: {stderr}"
        )

    lines = stdout.splitlines()

    for index, line in enumerate(lines):
        line = line.strip()

        if line in (
            "Hardware Port: Wi-Fi",
            "Hardware Port: AirPort"
        ):
            if index + 1 < len(lines):
                next_line = lines[index + 1].strip()

                if next_line.startswith("Device:"):
                    return next_line.split(":", 1)[1].strip()

    raise RuntimeError(
        "Could not automatically find the Mac Wi-Fi device"
    )


def get_interface_info(device):
    """
    Inspect the interface using ifconfig.

    Returns:
        active:      True if the interface is UP and RUNNING
        ip_address: IPv4 address, or None
    """

    code, stdout, stderr = run_command([
        "/sbin/ifconfig",
        device
    ])

    if code != 0:
        return False, None

    # First line looks approximately like:
    #
    # en0: flags=88e3<UP,BROADCAST,SMART,RUNNING,...>
    #
    first_line = stdout.splitlines()[0] if stdout else ""

    active = (
        "UP" in first_line
        and "RUNNING" in first_line
    )

    # Find a real IPv4 `inet` line.
    #
    # Important: don't accidentally match inet6.
    match = re.search(
        r"^\s*inet\s+([0-9.]+)",
        stdout,
        flags=re.MULTILINE
    )

    ip_address = match.group(1) if match else None

    return active, ip_address


# ============================================================
# HOTSPOT RECONNECT
# ============================================================

def join_hotspot(device):
    """
    Ask macOS to join the configured iPhone hotspot.
    """

    print(
        f"{timestamp()}  attempting hotspot reconnect..."
    )

    code, stdout, stderr = run_command([
        "/usr/sbin/networksetup",
        "-setairportnetwork",
        device,
        HOTSPOT_NAME,
        HOTSPOT_PASSWORD
    ], timeout=20)

    success = code == 0

    if not success:
        print(
            f"{timestamp()}  reconnect command failed: "
            f"{stderr or stdout or 'unknown error'}"
        )

    return success


# ============================================================
# INTERNET CONNECTIVITY
# ============================================================

def internet_check():
    """
    Perform a tiny HTTP request.

    This distinguishes an interface connection from actual
    working internet access.
    """

    started = time.monotonic()

    code, stdout, stderr = run_command([
        "/usr/bin/curl",
        "--silent",
        "--output", "/dev/null",
        "--connect-timeout", "3",
        "--max-time", "5",
        "https://www.cloudflare.com/cdn-cgi/trace"
    ], timeout=7)

    elapsed = time.monotonic() - started

    return {
        "success": code == 0,
        "elapsed_seconds": round(elapsed, 3),
        "error": "" if code == 0 else (
            stderr or f"curl exit code {code}"
        )
    }


# ============================================================
# DOWNLOAD SPEED TEST
# ============================================================

def speed_test():

    started = time.monotonic()

    code, stdout, stderr = run_command([
        "/usr/bin/curl",
        "--location",
        "--silent",
        "--output", "/dev/null",
        "--connect-timeout", "5",
        "--max-time", str(DOWNLOAD_TIMEOUT_SECONDS),
        "--write-out",
        "%{http_code},"
        "%{time_namelookup},"
        "%{time_connect},"
        "%{time_starttransfer},"
        "%{speed_download}",
        TEST_URL
    ], timeout=DOWNLOAD_TIMEOUT_SECONDS + 3)

    elapsed = time.monotonic() - started

    if code != 0:
        return {
            "success": False,
            "elapsed_seconds": round(elapsed, 3),
            "error": stderr or f"curl exit code {code}"
        }

    try:
        (
            http_code,
            dns_time,
            connect_time,
            ttfb,
            bytes_per_second
        ) = stdout.split(",")

        mbps = float(bytes_per_second) * 8 / 1_000_000

        return {
            "success": http_code == "200",
            "http_code": http_code,
            "dns_seconds": round(float(dns_time), 3),
            "connect_seconds": round(float(connect_time), 3),
            "ttfb_seconds": round(float(ttfb), 3),
            "elapsed_seconds": round(elapsed, 3),
            "mbps": round(mbps, 3),
            "error": ""
        }

    except Exception as exc:
        return {
            "success": False,
            "elapsed_seconds": round(elapsed, 3),
            "error": f"Could not parse curl result: {exc}"
        }


# ============================================================
# CSV
# ============================================================

CSV_FIELDS = [
    "timestamp",
    "interface_active",
    "ip_address",
    "internet_reachable",
    "reconnect_attempted",
    "reconnect_command_success",
    "download_success",
    "http_code",
    "dns_seconds",
    "connect_seconds",
    "ttfb_seconds",
    "elapsed_seconds",
    "mbps",
    "error"
]


def open_log():
    exists = os.path.exists(OUTPUT_FILE)

    log_file = open(
        OUTPUT_FILE,
        "a",
        newline="",
        buffering=1
    )

    writer = csv.DictWriter(
        log_file,
        fieldnames=CSV_FIELDS
    )

    if not exists or os.path.getsize(OUTPUT_FILE) == 0:
        writer.writeheader()

    return log_file, writer


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    global WIFI_DEVICE

    if WIFI_DEVICE is None:
        WIFI_DEVICE = find_wifi_device()

    print()
    print("Train network monitor")
    print("=====================")
    print()
    print(f"Interface       : {WIFI_DEVICE}")
    print(f"Hotspot         : {HOTSPOT_NAME}")
    print(f"Status check    : every {CHECK_INTERVAL_SECONDS}s")
    print(f"Speed test      : every {SPEED_TEST_INTERVAL_SECONDS}s")
    print(f"Output          : {OUTPUT_FILE}")
    print()
    print("Press Ctrl-C to stop.")
    print()

    log_file, writer = open_log()

    last_speed_test = 0

    try:

        while True:

            interface_active, ip_address = get_interface_info(
                WIFI_DEVICE
            )

            reconnect_attempted = False
            reconnect_command_success = False

            # ------------------------------------------------
            # Interface isn't associated / configured.
            # ------------------------------------------------

            if not interface_active or not ip_address:

                print(
                    f"{timestamp()}  HOTSPOT CONNECTION LOST  "
                    f"active={interface_active} "
                    f"ip={ip_address}"
                )

                reconnect_attempted = True

                reconnect_command_success = join_hotspot(
                    WIFI_DEVICE
                )

                time.sleep(3)

                interface_active, ip_address = get_interface_info(
                    WIFI_DEVICE
                )

                if interface_active and ip_address:

                    print(
                        f"{timestamp()}  HOTSPOT RECONNECTED  "
                        f"ip={ip_address}"
                    )

                else:

                    print(
                        f"{timestamp()}  still disconnected  "
                        f"active={interface_active} "
                        f"ip={ip_address}"
                    )

            # ------------------------------------------------
            # Internet test.
            # ------------------------------------------------

            if interface_active and ip_address:

                internet_result = internet_check()
                internet_reachable = internet_result["success"]

            else:

                internet_reachable = False

            # ------------------------------------------------
            # Speed test every configured interval.
            # ------------------------------------------------

            now_monotonic = time.monotonic()

            if (
                now_monotonic - last_speed_test
                >= SPEED_TEST_INTERVAL_SECONDS
            ):

                last_speed_test = now_monotonic

                if not interface_active or not ip_address:

                    result = {
                        "success": False,
                        "error": "Hotspot disconnected"
                    }

                elif not internet_reachable:

                    result = {
                        "success": False,
                        "error": (
                            "Interface connected but "
                            "internet unavailable"
                        )
                    }

                else:

                    result = speed_test()

                row = {
                    "timestamp": timestamp(),
                    "interface_active": interface_active,
                    "ip_address": ip_address or "",
                    "internet_reachable": internet_reachable,
                    "reconnect_attempted": reconnect_attempted,
                    "reconnect_command_success":
                        reconnect_command_success,
                    "download_success":
                        result.get("success", False),
                    "http_code":
                        result.get("http_code", ""),
                    "dns_seconds":
                        result.get("dns_seconds", ""),
                    "connect_seconds":
                        result.get("connect_seconds", ""),
                    "ttfb_seconds":
                        result.get("ttfb_seconds", ""),
                    "elapsed_seconds":
                        result.get("elapsed_seconds", ""),
                    "mbps":
                        result.get("mbps", ""),
                    "error":
                        result.get("error", "")
                }

                writer.writerow(row)
                log_file.flush()

                if result.get("success"):

                    print(
                        f"{row['timestamp']}  "
                        f"{result['mbps']:7.2f} Mbps  "
                        f"ip={ip_address}"
                    )

                elif not interface_active or not ip_address:

                    print(
                        f"{row['timestamp']}  "
                        f"HOTSPOT DISCONNECTED"
                    )

                elif not internet_reachable:

                    print(
                        f"{row['timestamp']}  "
                        f"NO INTERNET "
                        f"(interface still connected)"
                    )

                else:

                    print(
                        f"{row['timestamp']}  "
                        f"DOWNLOAD FAILED: "
                        f"{result.get('error', '')}"
                    )

            time.sleep(CHECK_INTERVAL_SECONDS)

    except KeyboardInterrupt:

        print()
        print("Stopped.")

    finally:

        log_file.close()


if __name__ == "__main__":
    main()

