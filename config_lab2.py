"""
config_lab2.py
==============
Full switching configuration + tests of the GNS3 IOU lab described in "prompt 1.md"
(updated version: 27 tasks, router IPs, interactive run).

Same pattern as connect.py (keep both files in the same folder):
  * connect()          -> telnet console sessions, no username / password / secret
  * test_connection()  -> every console is tested BEFORE anything is configured
  * GREEN = OK, RED = failed (the command output is printed when a test fails)
  * every test line starts with a timestamp
  * before each task the script shows the next step and asks:
        c = continue   s = stop   a = run the whole script without asking again
  * a SUCCESS / FAILED / NOTE summary is printed at the end

Run:   python config_lab2.py
Needs: pip install netmiko   (version 4 or newer)
"""
import ipaddress
import re
import shutil
import time
from datetime import datetime

from connect import connect, test_connection, DEVICES, GREEN, RED, RESET

# extra colors for the welcome banner and the questions (connect.py only has GREEN/RED)
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
CYAN = "\033[96m"

# =============================================================================
# 1) LAB SETTINGS - edit here, the rest of the script only reads these values
# =============================================================================
AUTHOR = "Mustafa Ismail"
SCRIPT_DATE = "25-oct-2026"

# Task 1 also sets "logging synchronous" + "exec-timeout 0 0" on line con 0 of every device.
# Reason: asynchronous console messages (%LINK-3-UPDOWN, %SYS-5-CONFIG_I ...) can cut the text
# that Netmiko types and make it fail with "Pattern not detected: 'configure\\ terminal'".
PREPARE_CONSOLE = True

ROUTER = "R1"
SWITCHES = ["IOU1", "IOU2", "IOU3", "IOU4", "IOU5"]
VTP_CLIENTS = ["IOU2", "IOU3", "IOU4", "IOU5"]          # IOU1 is the VTP server

# --- Task 20: IPs of the router R1
ROUTER_LINKS = {
    "FastEthernet0/0": "10.0.0.5/30",       # R1 <-> IOU1
    "FastEthernet1/0": "10.0.0.1/30",       # R1 <-> IOU2
}

# --- Tasks 2 and 24: routed port e0/0 of IOU1 / IOU2 (IPs from the table of task 24)
ROUTED_LINKS = {
    "IOU1": {"intf": "Ethernet0/0", "cidr": "10.0.0.6/30", "peer": "R1", "peer_ip": "10.0.0.5"},
    "IOU2": {"intf": "Ethernet0/0", "cidr": "10.0.0.2/30", "peer": "R1", "peer_ip": "10.0.0.1"},
}

# --- Task 4: native VLAN (IOS names cannot contain spaces -> underscore)
NATIVE_VLAN = 90
NATIVE_NAME = "native_vlan"

# --- Task 6: trunk ports per switch
TRUNK_PORTS = {
    "IOU1": ["e1/0", "e1/1", "e2/0", "e2/1", "e2/2"],
    "IOU2": ["e1/0", "e1/1", "e2/0", "e2/1", "e2/2"],
    "IOU3": ["e0/0", "e0/1"],
    "IOU4": ["e0/0", "e0/1"],
    "IOU5": ["e0/0", "e0/1"],
}

# --- Task 6b / 7: EtherChannel between IOU1 and IOU2
ETHERCHANNEL_PORTS = {"IOU1": ["e1/0", "e1/1"], "IOU2": ["e1/0", "e1/1"]}
CHANNEL_GROUP = 1
CHANNEL_MODE = "active"

# --- Task 10: VTP
VTP = {"domain": "test.local", "version": 2, "password": "123"}

# --- Task 12: VLANs / subnetting of 172.16.1.0/24
VLAN_NAMES = ["HR", "SALES", "PR", "MANAGEMENT", "DR",
              "OM", "VOICE", "IT", "CS", "DEV"]
VLAN_FIRST_ID = 10          # VLAN ids: 10, 20, ... (VLAN 90 is skipped: it is the native VLAN)
VLAN_ID_STEP = 10
BASE_NETWORK = "172.16.1.0/24"
SUBNET_PREFIX = 28          # 10 subnets need 4 bits -> /28 (16 subnets, 14 hosts each)

# --- Task 19: HSRP (IOU1 active for the first 5 VLANs, IOU2 for the last 5)
HSRP_GROUP = 10
HSRP_ACTIVE_PRIORITY = 150
HSRP_ACTIVE_ON = {"IOU1": VLAN_NAMES[:5], "IOU2": VLAN_NAMES[5:]}

# --- Task 9: syslog patterns that mean a problem
BAD_SYSLOG = ["NATIVE_VLAN_MISMATCH", "RECV_PVID_ERR", "BLOCK_PVID",
              "ERR_DISABLE", "CANNOT_BUNDLE", "ERRPROT", "L3DONTBNDL"]

# =============================================================================
# 2) HELPERS (timestamps, colored reports, config push, tests)
# =============================================================================
conns = {}      # device name -> netmiko connection
results = {}    # task id -> {"desc": str, "ok": int, "fail": [notes]}
PLAN = []       # VLAN plan, filled by task 12


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def short_error(e):
    """First non-empty line of an exception (Netmiko messages start with an empty line)."""
    lines = [l.strip() for l in str(e).splitlines() if l.strip()]
    return f"{type(e).__name__}: {lines[0]}" if lines else type(e).__name__


def warn(message):
    """Yellow timestamped warning (not a test result, not counted in the summary)."""
    print(f"{YELLOW}[{now()}] WARN {message}{RESET}")


def split_cidr(cidr):
    """'10.0.0.6/30' -> ('10.0.0.6', '255.255.255.252')."""
    iface = ipaddress.ip_interface(cidr)
    return str(iface.ip), str(iface.network.netmask)


def report(tid, ok, message, output=""):
    """Print one timestamped result (green/red) and store it for the summary."""
    color = GREEN if ok else RED
    mark = "OK  " if ok else "FAIL"
    print(f"{color}[{now()}] [T{tid}] {mark} {message}{RESET}")
    if output and not ok:
        print(f"{RED}{output.rstrip()}{RESET}")
    if ok:
        results[tid]["ok"] += 1
    else:
        results[tid]["fail"].append(message)


def config_errors(output):
    """Return IOS syntax errors found in the output of send_config_set().
    'switchport trunk encapsulation' is ignored: some IOU images do not have it."""
    errors, last = [], ""
    for line in output.splitlines():
        if "#" in line:
            last = line.split("#", 1)[1].strip()
        elif re.search(r"^%|not allowed|overlaps", line):
            # '% 10.0.0.4 overlaps with ...' and 'VTP VLAN configuration not allowed ...'
            # were silently ignored before and made later tests fail
            if "encapsulation" not in last:
                errors.append(f"'{last}' -> {line.strip()}")
    return errors


def raw_config(conn, commands, pause=0.5):
    """Fallback push that does NOT wait for the echo of each command.
    Used when send_config_set() loses sync with the console (see README2: Problem 1)."""
    conn.write_channel("\x1a")                  # Ctrl+Z: leave a config mode left by the failed try
    time.sleep(1)
    conn.clear_buffer()                         # throw away the half-read output
    conn.write_channel("configure terminal\n")
    time.sleep(1)
    for cmd in commands:
        conn.write_channel(cmd + "\n")
        time.sleep(pause)
    conn.write_channel("end\n")
    time.sleep(2)
    output = conn.read_channel()
    conn.clear_buffer()
    try:
        conn.find_prompt()                      # let Netmiko learn the prompt again
    except Exception:
        pass
    return output


def push_config(dev, commands):
    """Send configuration commands to `dev` and return the output.
    1) normal way: send_config_set();  2) if it fails: re-sync + raw_config()."""
    conn = conns[dev]
    try:
        return conn.send_config_set(commands)
    except Exception as e:
        warn(f"{dev}: send_config_set failed ({short_error(e)}) - "
             f"re-syncing the console and sending the commands again without echo check")
        return raw_config(conn, commands)


def apply(tid, dev, commands, ok_msg):
    """Push configuration commands to one device and report the result."""
    conn = conns.get(dev)
    if conn is None:
        report(tid, False, f"{dev}: not connected, skipped")
        return False
    try:
        errors = config_errors(push_config(dev, commands))
    except Exception as e:                      # one broken device must not stop the others
        report(tid, False, f"{dev}: cannot configure ({short_error(e)})")
        return False
    if errors:
        report(tid, False, f"{dev}: configuration errors", "\n".join(errors))
        return False
    report(tid, True, f"{dev}: {ok_msg}")
    return True


def send(conn, command, tries=3, **kwargs):
    """send_command() that re-syncs the console and retries on 'echo was not detected'
    (log messages such as %LINK-3-UPDOWN right after 'no shutdown' can swallow the echo)."""
    for attempt in range(tries):
        try:
            return conn.send_command(command, **kwargs)
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(3)
            conn.write_channel("\n")
            time.sleep(1)
            conn.clear_buffer()


def wait_for(conn, command, check, timeout=60, interval=5):
    """Run `command` until check(output) is True or the timeout is reached."""
    end = time.time() + timeout
    while True:
        out = send(conn, command)
        if check(out):
            return True, out
        if time.time() >= end:
            return False, out
        time.sleep(interval)


def test(tid, dev, command, check, ok_msg, fail_msg, wait=0, **kwargs):
    """Run a show/ping command and check the output: green if ok, red + output if not."""
    conn = conns.get(dev)
    if conn is None:
        report(tid, False, f"{dev}: not connected, test skipped")
        return False
    try:
        if wait:
            ok, out = wait_for(conn, command, check, timeout=wait)
        else:
            out = send(conn, command, **kwargs)
            ok = check(out)
    except Exception as e:                      # one broken device must not stop the others
        report(tid, False, f"{fail_msg} ({short_error(e)})")
        return False
    report(tid, ok, ok_msg if ok else fail_msg, out)
    return ok


def vlan_row(output, vlan_id, name):
    """True if 'show vlan brief' has an active row for this VLAN id and name."""
    return bool(re.search(rf"^{vlan_id}\s+{re.escape(name)}\s+active", output, re.M | re.I))


def ping_ok(output):
    """Ping passes with >= 80 % success (the first packet can be lost to ARP)."""
    m = re.search(r"Success rate is (\d+) percent", output)
    return bool(m) and int(m.group(1)) >= 80


def interface_up_with_ip(output, intf, ip):
    """Check 'show ip int br': right IP and status/protocol up/up.
    `intf` is the full name shown by IOS (Ethernet0/0, FastEthernet0/0)."""
    for line in output.splitlines():
        parts = line.split()
        if parts and parts[0] == intf:
            return parts[1] == ip and parts[-2] == "up" and parts[-1] == "up"
    return False


def test_ip_with_no_shut(tid, dev, intf, ip):
    """Tasks 23 / 25 - 'show ip int br' must show the IP and up/up.
    If not, run 'no shutdown' on the interface and test again."""
    conn = conns.get(dev)
    if conn is None:
        report(tid, False, f"{dev}: not connected, test skipped")
        return

    def check(out):
        return interface_up_with_ip(out, intf, ip)

    try:
        out = send(conn, "show ip interface brief")
        if check(out):
            report(tid, True, f"{dev}: {intf} has {ip} and is up/up")
            return
        push_config(dev, [f"interface {intf}", "no shutdown"])      # second chance
        ok, out = wait_for(conn, "show ip interface brief", check, timeout=30)
    except Exception as e:      # report this interface and go on with the next one
        report(tid, False, f"{dev}: {intf} test error ({short_error(e)})")
        return
    report(tid, ok,
           f"{dev}: {intf} has {ip} and is up/up (after no shutdown)" if ok
           else f"{dev}: {intf} does not have {ip} up/up, even after no shutdown", out)


def hsrp_state_ok(output, vlan_id, expected):
    """Check 'show standby brief': state of the VLAN interface (Active/Standby)."""
    m = re.search(rf"^Vl{vlan_id}\s+{HSRP_GROUP}\s+\d+\s+P?\s*(\w+)", output, re.M)
    return bool(m) and m.group(1) == expected


# =============================================================================
# 3) TASKS (numbers follow prompt 1.md; the prompt has no task 21 and 22)
# =============================================================================
def prepare_console(dev):
    """Quiet console: 'logging synchronous' keeps log messages from cutting the typed command,
    'exec-timeout 0 0' keeps the console session from logging out while the script waits."""
    try:
        push_config(dev, ["line con 0", "logging synchronous", "exec-timeout 0 0"])
        print(f"[{now()}] {dev}: console prepared (logging synchronous, exec-timeout 0 0)")
    except Exception as e:
        warn(f"{dev}: could not prepare the console ({short_error(e)}), going on without it")


def task1_connect(tid):
    """Task 1 - connect to R1 and every IOU with connect.py and test each console."""
    for name in [ROUTER] + SWITCHES:
        if name not in DEVICES:
            report(tid, False, f"{name}: missing in DEVICES of connect.py "
                               f"(add  \"{name}\": (host, port))")
            continue
        try:
            conn = connect(name)
        except Exception as e:
            report(tid, False, f"{name}: cannot connect ({e})")
            continue
        conns[name] = conn
        ok = test_connection(conn)              # prints the IOS version line
        report(tid, ok, f"{name}: console test {'passed' if ok else 'FAILED'}")
        if ok and PREPARE_CONSOLE:
            prepare_console(name)


def task2_routed_ports(tid):
    """Task 2 - e0/0 of IOU1/IOU2: no switchport, then add the IP."""
    for dev, link in ROUTED_LINKS.items():
        ip, mask = split_cidr(link["cidr"])
        apply(tid, dev, [
            "ip routing",
            f"interface {link['intf']}",
            "no switchport",
            f"ip address {ip} {mask}",
            "no shutdown",
        ], f"{link['intf']} routed, {ip} {mask}")
    time.sleep(5)


def task3_test_ips(tid):
    """Task 3 - 'show ip int br' must show the IP and up/up."""
    for dev, link in ROUTED_LINKS.items():
        ip = split_cidr(link["cidr"])[0]
        test(tid, dev, "show ip interface brief",
             lambda out, l=link, i=ip: interface_up_with_ip(out, l["intf"], i),
             f"{dev}: {link['intf']} has {ip} and is up/up",
             f"{dev}: {link['intf']} does not have {ip} up/up")


def task4_native_vlan(tid):
    """Task 4 - create VLAN 90 on all IOU (also enable the log buffer for task 9)."""
    for dev in SWITCHES:
        # A switch left in VTP client mode by an earlier run refuses 'vlan 90'
        # ("VTP VLAN configuration not allowed when device is in CLIENT mode").
        # Transparent mode allows it; task 10 sets the final mode (client) afterwards.
        mode = [] if dev == "IOU1" else ["vtp mode transparent"]
        apply(tid, dev, mode + [
            "logging buffered 16384 debugging",
            f"vlan {NATIVE_VLAN}",
            f"name {NATIVE_NAME}",
        ], f"VLAN {NATIVE_VLAN} ({NATIVE_NAME}) created")


def task5_test_native_vlan(tid):
    """Task 5 - 'show vlan brief' must list VLAN 90."""
    for dev in SWITCHES:
        test(tid, dev, "show vlan brief",
             lambda out: vlan_row(out, NATIVE_VLAN, NATIVE_NAME),
             f"{dev}: VLAN {NATIVE_VLAN} present",
             f"{dev}: VLAN {NATIVE_VLAN} not found")


def task6_trunks(tid):
    """Task 6 - trunk ports with native VLAN 90."""
    for dev, ports in TRUNK_PORTS.items():
        commands = []
        for port in ports:
            commands += [
                f"interface {port}",
                "switchport trunk encapsulation dot1q",   # ignored if not supported
                f"switchport trunk native vlan {NATIVE_VLAN}",
                "switchport mode trunk",
                "no shutdown",
            ]
        apply(tid, dev, commands, f"trunks on {', '.join(ports)}")


def task6b_etherchannel(tid):
    """Task 6 (second item) - EtherChannel IOU1/IOU2: channel-group 1 mode active."""
    for dev, ports in ETHERCHANNEL_PORTS.items():
        commands = []
        for port in ports:
            commands += [f"interface {port}",
                         f"channel-group {CHANNEL_GROUP} mode {CHANNEL_MODE}"]
        apply(tid, dev, commands,
              f"{', '.join(ports)} in channel-group {CHANNEL_GROUP} mode {CHANNEL_MODE}")


def task7_po1(tid):
    """Task 7 - Po1 as trunk with native VLAN 90."""
    for dev in ETHERCHANNEL_PORTS:
        apply(tid, dev, [
            f"interface port-channel {CHANNEL_GROUP}",
            "switchport trunk encapsulation dot1q",       # ignored if not supported
            f"switchport trunk native vlan {NATIVE_VLAN}",
            "switchport mode trunk",
        ], f"Po{CHANNEL_GROUP} trunk, native VLAN {NATIVE_VLAN}")


def task8_test_etherchannel(tid):
    """Task 8 - 'show etherchannel summary': Po1 (SU) with both members (P)."""
    def etherchannel_ok(out):
        return (re.search(rf"Po{CHANNEL_GROUP}\(SU\)", out) is not None
                and len(re.findall(r"\(P\)", out)) >= 2)
    for dev in ETHERCHANNEL_PORTS:
        test(tid, dev, "show etherchannel summary", etherchannel_ok,
             f"{dev}: Po{CHANNEL_GROUP} is up (SU) with bundled ports (P)",
             f"{dev}: Po{CHANNEL_GROUP} is not up / ports not bundled", wait=60)


def task9_test_syslog(tid):
    """Task 9 - no native VLAN mismatch or EtherChannel problems in the syslog."""
    for dev in SWITCHES:
        conn = conns.get(dev)
        if conn is None:
            report(tid, False, f"{dev}: not connected, test skipped")
            continue
        log = conn.send_command("show logging")
        bad_lines = [l for l in log.splitlines() if any(p in l for p in BAD_SYSLOG)]
        if bad_lines:
            report(tid, False, f"{dev}: problem messages in syslog", "\n".join(bad_lines))
        else:
            report(tid, True, f"{dev}: no native VLAN mismatch / EtherChannel errors in syslog")


def task10_vtp(tid):
    """Task 10 - VTP server on IOU1 (the other switches are set as clients)."""
    apply(tid, "IOU1", [
        "vtp mode server",
        f"vtp version {VTP['version']}",
        f"vtp domain {VTP['domain']}",
        f"vtp password {VTP['password']}",
    ], f"VTP server, domain {VTP['domain']}, v{VTP['version']}")
    for dev in VTP_CLIENTS:
        apply(tid, dev, [
            f"vtp version {VTP['version']}",
            f"vtp password {VTP['password']}",
            "vtp mode client",
        ], "VTP client")


def task11_test_vtp(tid):
    """Task 11 - every IOU must list the VTP domain; if not, set the domain again."""
    def domain_ok(out):
        m = re.search(r"VTP Domain Name\s*:\s*(\S+)", out)
        return bool(m) and m.group(1) == VTP["domain"]

    for dev in SWITCHES:
        conn = conns.get(dev)
        if conn is None:
            report(tid, False, f"{dev}: not connected, test skipped")
            continue
        ok, out = wait_for(conn, "show vtp status", domain_ok, timeout=15)
        if not ok:   # domain not learned -> execute the domain command again
            push_config(dev, [f"vtp domain {VTP['domain']}", "vtp mode client"
                              if dev in VTP_CLIENTS else "vtp mode server"])
            ok, out = wait_for(conn, "show vtp status", domain_ok, timeout=15)
        report(tid, ok,
               f"{dev}: VTP domain {VTP['domain']} listed" if ok
               else f"{dev}: VTP domain {VTP['domain']} NOT listed", out)


def task12_subnetting(tid):
    """Task 12 - split 172.16.1.0/24 into 10 VLAN subnets (/28)."""
    subnets = list(ipaddress.ip_network(BASE_NETWORK).subnets(new_prefix=SUBNET_PREFIX))
    PLAN.clear()
    vlan_ids = [i for i in range(VLAN_FIRST_ID, 1000, VLAN_ID_STEP) if i != NATIVE_VLAN]
    for i, name in enumerate(VLAN_NAMES):
        net = subnets[i]
        hosts = list(net.hosts())
        PLAN.append({
            "id": vlan_ids[i], "name": name, "net": net,
            "mask": str(net.netmask),
            "vip": str(hosts[0]),       # 1st host  = HSRP virtual IP
            "IOU1": str(hosts[1]),      # 2nd host  = IOU1
            "IOU2": str(hosts[2]),      # 3rd host  = IOU2
        })
    print(f"[{now()}] VLAN plan from {BASE_NETWORK} (/{SUBNET_PREFIX}):")
    for v in PLAN:
        print(f"    VLAN {v['id']:<4}{v['name']:<11}{str(v['net']):<16}"
              f"VIP {v['vip']:<13}IOU1 {v['IOU1']:<13}IOU2 {v['IOU2']}")
    report(tid, True, f"{len(PLAN)} subnets of /{SUBNET_PREFIX} created from {BASE_NETWORK}")


def task13_create_vlans(tid):
    """Task 13 - create the VLANs on IOU1 only (VTP server)."""
    commands = []
    for v in PLAN:
        commands += [f"vlan {v['id']}", f"name {v['name']}"]
    apply(tid, "IOU1", commands, f"{len(PLAN)} VLANs created")


def task14_test_vlans_iou1(tid):
    """Task 14 - all VLANs must be in 'show vlan brief' on IOU1."""
    test(tid, "IOU1", "show vlan brief",
         lambda out: all(vlan_row(out, v["id"], v["name"]) for v in PLAN),
         "IOU1: all VLANs created", "IOU1: some VLANs are missing")


def task15_test_vtp_clients(tid):
    """Task 15 - the client IOU must learn the VLANs created on IOU1."""
    for dev in VTP_CLIENTS:
        test(tid, dev, "show vlan brief",
             lambda out: all(vlan_row(out, v["id"], v["name"]) for v in PLAN),
             f"{dev}: learned all VLANs from IOU1 via VTP",
             f"{dev}: did not learn all VLANs via VTP", wait=60)


def task16_svis(tid):
    """Task 16 - 2nd host on IOU1, 3rd host on IOU2, interfaces no shutdown."""
    for dev in ("IOU1", "IOU2"):
        commands = []
        for v in PLAN:
            commands += [f"interface vlan {v['id']}",
                         f"ip address {v[dev]} {v['mask']}",
                         "no shutdown"]
        apply(tid, dev, commands, f"{len(PLAN)} VLAN interfaces up with IPs")
    time.sleep(10)


def task_ping_vlans(tid):
    """Tasks 17 / 26 - ping IOU2 from IOU1 in every VLAN."""
    for v in PLAN:
        test(tid, "IOU1", f"ping {v['IOU2']} repeat 5", ping_ok,
             f"VLAN {v['id']} {v['name']}: IOU1 -> IOU2 ({v['IOU2']}) reachable",
             f"VLAN {v['id']} {v['name']}: IOU1 -> IOU2 ({v['IOU2']}) FAILED",
             read_timeout=60)


def task_ping_routers(tid):
    """Tasks 18 / 27 - ping R1 from IOU1 and from IOU2 (the lab has only R1).
    Task 18 runs before task 20, so R1 gets its IPs here first if it has none yet."""
    if tid == "18" and ROUTER in conns:
        warn("R1 gets its IPs now (task 20 comes later), otherwise this ping cannot work")
        push_config(ROUTER, [x for i, c in ROUTER_LINKS.items() for x in
                             (f"interface {i}", "no ip address")] +
                            [x for i, c in ROUTER_LINKS.items() for x in
                             (f"interface {i}", f"ip address {split_cidr(c)[0]} {split_cidr(c)[1]}",
                              "no shutdown")])
        time.sleep(5)
    for dev, link in ROUTED_LINKS.items():
        test(tid, dev, f"ping {link['peer_ip']} repeat 5", ping_ok,
             f"{dev} -> {link['peer']} ({link['peer_ip']}) reachable",
             f"{dev} -> {link['peer']} ({link['peer_ip']}) FAILED",
             read_timeout=60)


def task19_hsrp(tid):
    """Task 19 - HSRP: IOU1 active for 5 VLANs, IOU2 active for the other 5."""
    for dev in ("IOU1", "IOU2"):
        commands = []
        for v in PLAN:
            commands += [f"interface vlan {v['id']}",
                         f"standby {HSRP_GROUP} ip {v['vip']}"]
            if v["name"] in HSRP_ACTIVE_ON[dev]:
                commands.append(f"standby {HSRP_GROUP} priority {HSRP_ACTIVE_PRIORITY}")
            commands.append(f"standby {HSRP_GROUP} preempt")
        apply(tid, dev, commands, "HSRP configured")
    # check that every VLAN has the expected HSRP state on each switch
    for dev in ("IOU1", "IOU2"):
        for v in PLAN:
            expected = "Active" if v["name"] in HSRP_ACTIVE_ON[dev] else "Standby"
            test(tid, dev, "show standby brief",
                 lambda out, vid=v["id"], e=expected: hsrp_state_ok(out, vid, e),
                 f"{dev}: VLAN {v['id']} {v['name']} is {expected}",
                 f"{dev}: VLAN {v['id']} {v['name']} is not {expected}", wait=60)


def task20_router_ips(tid):
    """Task 20 - assign the IPs on R1 ('ip address'; 'no shutdown' is left to task 23)."""
    commands, shown = [], []
    for intf, cidr in ROUTER_LINKS.items():
        ip, mask = split_cidr(cidr)
        # old IPs left in NVRAM (F0/0 10.0.0.1, F1/0 10.0.0.5) would overlap with the new
        # subnets and IOS would reject 'ip address', so they are removed first
        commands += [f"interface {intf}", "no ip address"]
    for intf, cidr in ROUTER_LINKS.items():
        ip, mask = split_cidr(cidr)
        commands += [f"interface {intf}", f"ip address {ip} {mask}"]
        shown.append(f"{intf} {cidr}")
    apply(tid, ROUTER, commands, "IPs assigned: " + ", ".join(shown))


def task23_test_router_ips(tid):
    """Task 23 - R1 interfaces must have the IP and be up/up (else no shutdown + retest)."""
    for intf, cidr in ROUTER_LINKS.items():
        test_ip_with_no_shut(tid, ROUTER, intf, split_cidr(cidr)[0])


def task24_switch_ips(tid):
    """Task 24 - assign the IPs on IOU1 / IOU2 ('ip address' again, same as task 2)."""
    for dev, link in ROUTED_LINKS.items():
        ip, mask = split_cidr(link["cidr"])
        apply(tid, dev, [f"interface {link['intf']}", f"ip address {ip} {mask}"],
              f"{link['intf']} {ip} {mask}")


def task25_test_switch_ips(tid):
    """Task 25 - IOU1 / IOU2 interfaces must have the IP and be up/up (else no shutdown + retest)."""
    for dev, link in ROUTED_LINKS.items():
        test_ip_with_no_shut(tid, dev, link["intf"], split_cidr(link["cidr"])[0])


# (task id, description, function) - the order of prompt 1.md
TASKS = [
    ("1",  "Connect to R1 + all IOU and test the consoles", task1_connect),
    ("2",  "e0/0 on IOU1/IOU2: no switchport + IPs", task2_routed_ports),
    ("3",  "Test IPs (show ip int br)", task3_test_ips),
    ("4",  "Create native VLAN 90 on all IOU", task4_native_vlan),
    ("5",  "Test VLAN 90 (show vlan brief)", task5_test_native_vlan),
    ("6",  "Trunk ports, native VLAN 90", task6_trunks),
    ("6b", "EtherChannel IOU1-IOU2 (mode active)", task6b_etherchannel),
    ("7",  "Po1 trunk, native VLAN 90", task7_po1),
    ("8",  "Test EtherChannel (show etherchannel summary)", task8_test_etherchannel),
    ("9",  "Test syslog (no mismatch / EtherChannel errors)", task9_test_syslog),
    ("10", "VTP server on IOU1 (clients on the others)", task10_vtp),
    ("11", "Test VTP domain on all IOU", task11_test_vtp),
    ("12", "Subnet 172.16.1.0/24 into 10 VLAN subnets", task12_subnetting),
    ("13", "Create VLANs on IOU1 only", task13_create_vlans),
    ("14", "Test VLANs on IOU1", task14_test_vlans_iou1),
    ("15", "Test VTP: client IOU learned the VLANs", task15_test_vtp_clients),
    ("16", "VLAN interface IPs (2nd/3rd host), no shut", task16_svis),
    ("17", "Ping IOU1 -> IOU2 in each VLAN", task_ping_vlans),
    ("18", "Ping IOU1 -> R1 and IOU2 -> R1", task_ping_routers),
    ("19", "HSRP + load balancing between VLANs", task19_hsrp),
    ("20", "Assign IPs on R1 (F0/0, F1/0)", task20_router_ips),
    ("23", "Test R1 IPs (no shut + retest if down)", task23_test_router_ips),
    ("24", "Assign IPs on IOU1 / IOU2", task24_switch_ips),
    ("25", "Test IOU1 / IOU2 IPs (no shut + retest)", task25_test_switch_ips),
    ("26", "Ping IOU1 -> IOU2 in each VLAN (final)", task_ping_vlans),
    ("27", "Ping IOU1 -> R1 and IOU2 -> R1 (final)", task_ping_routers),
]


# =============================================================================
# 4) WELCOME + QUESTIONS TO THE USER ("How the script works" of prompt 1.md)
# =============================================================================
def show_welcome():
    """Colorful welcome message in the center of the terminal, with author and date."""
    width = shutil.get_terminal_size((100, 20)).columns
    box = min(width, 70)
    lines = [
        "=" * box,
        "",
        "W E L C O M E",
        "",
        "This script is used to do the full switching configuration",
        "and tests of the GNS3 IOU lab",
        "",
        f"Author: {AUTHOR}",
        f"Date: {SCRIPT_DATE}",
        "",
        "=" * box,
    ]
    colors = [RED, YELLOW, GREEN, CYAN, BLUE, MAGENTA]
    print()
    for i, line in enumerate(lines):
        # center first, color after (the color codes would break the centering)
        print(f"{colors[i % len(colors)]}{line.center(width).rstrip()}{RESET}")
    print()


def ask(text):
    """input() that returns None when the user presses Ctrl+C / Ctrl+D."""
    try:
        return input(text).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def ask_yes_no(question):
    answer = ask(f"{YELLOW}{question} [y/N]: {RESET}")
    return answer in ("y", "yes")


def ask_next(tid, desc, state):
    """Show the name of the next step and ask: continue / stop / run all.
    Returns True to run the task, False to stop the script."""
    if state["all"]:
        print(f"\n{CYAN}[{now()}] Next: Task {tid} - {desc}{RESET}")
        return True
    while True:
        answer = ask(f"\n{CYAN}Next: Task {tid} - {desc}{RESET}\n"
                     f"{YELLOW}  [c] continue   [s] stop   [a] run the whole script > {RESET}")
        if answer in ("c", ""):             # Enter = continue
            return True
        if answer == "a":
            state["all"] = True
            return True
        if answer in ("s", None):
            return False
        print(f"{RED}Please type c, s or a.{RESET}")


# =============================================================================
# 5) SUMMARY + MAIN
# =============================================================================
def print_summary():
    """SUCCESS / FAILED / NOTE table of every task."""
    print("\n" + "=" * 110)
    print(f"SUMMARY  [{now()}]")
    print(f"{'TASK':<6}{'DESCRIPTION':<52}{'SUCCESS':<9}{'FAILED':<8}NOTE")
    print("-" * 110)
    for tid, desc, _ in TASKS:
        r = results.get(tid)
        if r is None:
            ok, note = False, "not performed"
        elif r["fail"]:
            ok, note = False, "; ".join(r["fail"][:3]) + (" ..." if len(r["fail"]) > 3 else "")
        else:
            ok, note = True, ""
        color = GREEN if ok else RED
        print(f"{color}{tid:<6}{desc:<52}{'YES' if ok else '':<9}"
              f"{'' if ok else 'YES':<8}{note}{RESET}")
    print("=" * 110)


def run_task(tid, desc, func):
    """Run one task; an unexpected error is reported and does not stop the script."""
    print(f"\n--- Task {tid}: {desc} ---")
    results[tid] = {"desc": desc, "ok": 0, "fail": []}
    try:
        func(tid)
    except Exception as e:
        report(tid, False, f"unexpected error: {short_error(e)}")


def main():
    show_welcome()
    if ask(f"{YELLOW}Press Enter to perform (Ctrl+C to quit) ... {RESET}") is None:
        return
    print(f"[{now()}] Starting lab configuration")
    state = {"all": False}
    try:
        # Task 1 = test the connection first, before anything is configured
        first_id, first_desc, first_func = TASKS[0]
        run_task(first_id, first_desc, first_func)
        proceed = True
        if results[first_id]["fail"]:
            print(f"{RED}Some consoles are not reachable (see the red lines above).{RESET}")
            proceed = ask_yes_no("Continue anyway?")

        # Tasks 2.. = ask before each one (continue / stop / run all)
        if proceed:
            for tid, desc, func in TASKS[1:]:
                if not ask_next(tid, desc, state):
                    print(f"{YELLOW}[{now()}] Stopped by the user before task {tid}.{RESET}")
                    break
                run_task(tid, desc, func)
        else:
            print(f"{YELLOW}[{now()}] Stopped by the user.{RESET}")
    except KeyboardInterrupt:
        print(f"\n{YELLOW}[{now()}] Interrupted (Ctrl+C).{RESET}")
    finally:
        print_summary()
        for conn in conns.values():
            try:
                conn.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    main()
