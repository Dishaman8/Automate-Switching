"""
connect.py - connect-only helper for GNS3 consoles (telnet, no username/password/secret).

Usage from another script:
    from connect import connect, test_connection, DEVICES
    conn = connect("R1")
    test_connection(conn)   # prints the IOS version if the connection works
    conn.disconnect()

To add a new device later: add one line to DEVICES as name -> (host, console port),
where host is "local" (127.0.0.1) or "vm" (192.168.56.101) and the port comes from
the GNS3 "Topology Summary" panel.
"""
import os
from netmiko import ConnectHandler

# ANSI colors (os.system("") enables them in the Windows terminal)
os.system("")
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

# GNS3 servers (where the consoles live)
HOSTS = {
    "local": "127.0.0.1",        # GNS3 on your local PC
    "vm": "192.168.56.101",      # GNS3 VM
}

# name -> (host key from HOSTS, telnet console port)
DEVICES = {
    "R1": ("local", 5004),
    "IOU1" : ("vm" , 5000),# "IOU1": ("vm", 5000),
    "IOU2": ("vm", 5001),
    "IOU3": ("vm", 5002),
    "IOU4": ("vm", 5003),
    "IOU5": ("vm", 5004),
}


def connect(name):
    """Open a telnet session to a device console and return the connection."""
    host_key, port = DEVICES[name]
    host = HOSTS[host_key]
    device = {
        "device_type": "cisco_ios_telnet",
        "host": host,
        "port": port,
        "username": "",   # no username
        "password": "",   # no password
        "secret": "",     # no enable secret
        "fast_cli": False,
        "global_delay_factor": 2,  # GNS3 consoles can be slow
    }
    conn = ConnectHandler(**device)
    print(f"[+] Connected to {name} ({host}:{port})")
    return conn


def test_connection(conn):
    """Run 'show version' and print the IOS version line. Returns True on success."""
    try:
        output = conn.send_command("show version")
    except Exception as e:
        print(f"{RED}[-] Test failed: {e}{RESET}")
        return False

    for line in output.splitlines():
        if "Cisco IOS Software" in line or "Version" in line:
            print(f"{GREEN}[+] Connection OK - {line.strip()}{RESET}")
            return True

    print(f"{RED}[-] Connected, but could not find the IOS version in the output{RESET}")
    print(output)
    return False


if __name__ == "__main__":
    for dev in DEVICES:
        try:
            c = connect(dev)
        except Exception as e:
            print(f"{RED}[-] Could not connect to {dev}: {e}{RESET}")
            continue
        test_connection(c)
        c.disconnect()