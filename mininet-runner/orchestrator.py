"""
Mininet Orchestrator — Stage 4: Experimental Validation
---------------------------------------------------------
Translates the verified JSON configuration into Mininet topology commands,
instantiates the emulated network, and verifies end-to-end connectivity
via ICMP (ping) tests.

Two modes:
  "real"      → Runs actual Mininet (requires Linux + root privileges)
  "simulated" → Returns a deterministic simulated result (for CI/demo)
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from typing import Dict

logger = logging.getLogger(__name__)


# ── Simulated mode ────────────────────────────────────────────────────────────
def _simulate_mininet(config: dict, policy_type: str) -> dict:
    """
    Simulate Mininet validation for environments without Mininet installed.
    Result probability is based on policy complexity (mirrors paper findings):
      Reachability:   ~80% success
      Waypoint:       ~20% success
      Load-Balancing: ~20% success
    """
    import hashlib, struct
    # Deterministic pseudo-random outcome based on config content
    seed = int(hashlib.md5(json.dumps(config, sort_keys=True).encode()).hexdigest()[:8], 16)

    thresholds = {"reachability": 0.80, "waypoint": 0.20, "load-balancing": 0.20}
    threshold  = thresholds.get(policy_type, 0.50)
    success    = (seed % 100) / 100.0 < threshold

    src = config.get("source", "host-a")
    dst = config.get("destination", "10.0.0.0/24")
    dst_host = dst.split("/")[0] if "/" in dst else dst

    if success:
        ping_out = (
            f"PING {dst_host} ({dst_host}) 56(84) bytes of data.\n"
            f"64 bytes from {dst_host}: icmp_seq=1 ttl=64 time=0.421 ms\n"
            f"64 bytes from {dst_host}: icmp_seq=2 ttl=64 time=0.398 ms\n"
            f"64 bytes from {dst_host}: icmp_seq=3 ttl=64 time=0.412 ms\n"
            f"--- {dst_host} ping statistics ---\n"
            f"3 packets transmitted, 3 received, 0% loss, time 2001ms"
        )
        return {
            "status": "pass",
            "detail": {
                "ping_output":  ping_out,
                "packet_loss":  0,
                "topology":     {"source": src, "destination": dst, "simulated": True},
            },
        }
    else:
        reason = "No route to host" if policy_type == "reachability" else "Waypoint unreachable"
        ping_out = (
            f"PING {dst_host} ({dst_host}) 56(84) bytes of data.\n"
            f"From {src} icmp_seq=1 Destination Host Unreachable\n"
            f"From {src} icmp_seq=2 Destination Host Unreachable\n"
            f"From {src} icmp_seq=3 Destination Host Unreachable\n"
            f"--- {dst_host} ping statistics ---\n"
            f"3 packets transmitted, 0 received, 100% loss, time 2001ms"
        )
        return {
            "status": "fail",
            "detail": {
                "ping_output": ping_out,
                "packet_loss": 100,
                "reason":      reason,
                "topology":    {"source": src, "destination": dst, "simulated": True},
            },
        }


# ── Real Mininet mode ─────────────────────────────────────────────────────────
MININET_SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
"""Auto-generated Mininet topology script — NetValidAI Stage 4"""
from mininet.net import Mininet
from mininet.node import OVSSwitch, DefaultController
from mininet.log import setLogLevel
import sys, json

def run():
    setLogLevel("warning")
    net = Mininet(switch=OVSSwitch, controller=DefaultController)
    net.addController("c0")

    src_name  = "{src}"
    dst_name  = "{dst}"
    src_ip    = "10.0.0.1"
    dst_ip    = "{dst_ip}"
    src_mask  = "10.0.0.1/24"
    dst_mask  = "{dst_ip}/{dst_prefix}"

    h1 = net.addHost(src_name, ip=src_mask)
    h2 = net.addHost(dst_name, ip=dst_mask)
    s1 = net.addSwitch("s1")

    net.addLink(h1, s1)
    net.addLink(h2, s1)

    net.start()

    # L2/L3 sanitization
    h1.cmd(f"ip route add {{dst_ip}}/{{dst_prefix}} dev {{src_name}}-eth0 2>/dev/null")
    h2.cmd("ip route add default dev {dst_name}-eth0 2>/dev/null")

    result = h1.cmd(f"ping -c 3 -W 2 {{dst_ip}}")
    loss   = 100 if "100% packet loss" in result else (
             0   if "0% packet loss"   in result else 50)

    net.stop()
    print(json.dumps({{"output": result, "loss": loss}}))

if __name__ == "__main__":
    run()
'''


def _run_real_mininet(config: dict, policy_type: str) -> dict:
    """Execute actual Mininet validation (requires Linux + root + Mininet installed)."""
    src = config.get("source", "srch")
    dst = config.get("destination", "10.0.1.0/24")

    # Resolve destination to IP + prefix
    if "/" in dst:
        parts = dst.split("/")
        dst_ip, dst_prefix = parts[0], parts[1]
    else:
        dst_ip, dst_prefix = dst, "32"

    # Sanitize host names for Mininet (must be alphanumeric, max 8 chars)
    src_clean = "".join(c for c in src if c.isalnum())[:8] or "srch"
    dst_clean = "dst" + dst_ip.replace(".", "")[-4:] if dst_ip else "dsth"

    script = MININET_SCRIPT_TEMPLATE.format(
        src=src_clean, dst=dst_clean,
        dst_ip=dst_ip, dst_prefix=dst_prefix,
    )

    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(script)
        script_path = f.name

    try:
        proc = subprocess.run(
            ["sudo", "python3", script_path],
            capture_output=True, text=True, timeout=60
        )
        output_raw = proc.stdout.strip()
        result_data = json.loads(output_raw) if output_raw else {}
        loss = result_data.get("loss", 100)
        ping_out = result_data.get("output", proc.stdout or proc.stderr)

        return {
            "status": "pass" if loss == 0 else "fail",
            "detail": {
                "ping_output": ping_out,
                "packet_loss": loss,
                "topology":    {"source": src, "destination": dst, "simulated": False},
            },
        }
    except subprocess.TimeoutExpired:
        return {"status": "fail", "detail": {"ping_output": "Timeout", "packet_loss": 100}}
    except Exception as e:
        logger.error(f"Mininet execution error: {e}")
        return {"status": "fail", "detail": {"ping_output": str(e), "packet_loss": 100}}
    finally:
        os.unlink(script_path)


# ── Public API ────────────────────────────────────────────────────────────────
async def run_mininet_validation(config: dict, policy_type: str, mode: str = "simulated") -> Dict:
    """
    Execute Stage 4 validation.

    Args:
        config:      Verified JSON configuration
        policy_type: reachability | waypoint | load-balancing
        mode:        "real" | "simulated"

    Returns:
        Stage result dict with status and detail.
    """
    logger.info(f"Stage 4 | mode={mode} policy={policy_type}")

    loop = asyncio.get_event_loop()

    if mode == "real":
        result = await loop.run_in_executor(None, _run_real_mininet, config, policy_type)
    else:
        result = await loop.run_in_executor(None, _simulate_mininet, config, policy_type)

    logger.info(f"Stage 4 result: {result['status']} | loss={result.get('detail',{}).get('packet_loss','?')}%")
    return result
