# Switching with AI

A GNS3 multilayer-switching lab (1 router + 5 IOU switches) that was **designed with the help of AI** and is
**configured and tested automatically with Python and the Netmiko library**.

## What the lab does
- Routed links R1 <-> IOU1 / IOU2 (10.0.0.4/30 and 10.0.0.0/30)
- Trunks with native VLAN 90 between all switches, and an EtherChannel (LACP, `Po1`) between IOU1 and IOU2
- VTP (IOU1 = server, IOU2-IOU5 = clients), 10 VLANs made by subnetting `172.16.1.0/24` into /28 subnets
- SVIs on IOU1 / IOU2 and HSRP load balancing (IOU1 active for 5 VLANs, IOU2 for the other 5)
- 26 tasks, each one followed by a test that prints green (OK) or red (failed + the device output), with a timestamp

## How it was built with AI
1. The topology and the task list were written down as a prompt (topology picture, port table, 27 numbered tasks).
2. The AI (Claude Code) turned the prompt into Python: `connect.py` opens the telnet consoles with Netmiko and
   `config_lab2.py` runs and tests every task.
3. The script was run against the real GNS3 lab, and the AI fixed what failed (stale VTP client mode,
   old IPs on R1, console timeouts) until all tasks passed.

## Files
| File | Purpose |
|------|---------|
| `connect.py` | Device list (`DEVICES`) and the Netmiko telnet connection helper |
| `config_lab2.py` | The full configuration + test script (welcome screen, asks before each task, summary table) |
| `auto-switching.gns3project` | The GNS3 project (portable, import it in GNS3) |
| `auto-pt1.zip` | Earlier version of the GNS3 project |

## How to use
1. Import `auto-switching.gns3project` in GNS3 (File > Import portable project) and start all nodes.
2. Install the requirement: `pip install netmiko` (version 4 or newer).
3. **Configure `connect.py` for your own machine (required).**
   The multilayer switches are IOU nodes, and their address and console ports are **different from machine to
   machine**. Open the *Topology Summary* panel in GNS3 and, for every device, copy the console host and port into `DEVICES`:
   ```python
   HOSTS = {
       "local": "127.0.0.1",        # GNS3 running on your PC
       "vm": "192.168.56.101",      # <-- put the IP of YOUR GNS3 VM here
   }
   DEVICES = {                      # name -> (host key, telnet console port)
       "R1":   ("local", 5004),
       "IOU1": ("vm", 5000),
       "IOU2": ("vm", 5001),
       "IOU3": ("vm", 5002),
       "IOU4": ("vm", 5003),
       "IOU5": ("vm", 5004),
   }
   ```
   Every device needs the right host (IP of the GNS3 VM or `127.0.0.1`) **and** its own console port.
4. Check the interface names and IPs at the top of `config_lab2.py` (`ROUTER_LINKS`, `ROUTED_LINKS`,
   `TRUNK_PORTS`) against your topology.
5. Run `python config_lab2.py`, press Enter, and answer `c` (continue), `s` (stop) or `a` (run everything).

## Notes
- The consoles have no username, password or enable secret.
- Task 18 gives R1 its IPs first because the task order pings R1 before task 20 assigns them.
- Tested on GNS3 with IOU L2/L3 images and a Dynamips c7200 router.

Author: Mustafa Ismail
