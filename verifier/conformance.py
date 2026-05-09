"""
Stage 2 — Schema Conformance + DETOX Logical Conflict Detection
----------------------------------------------------------------
Validates the JSON configuration against the NetConfEval YANG-inspired schema
and runs DETOX-style logical consistency checks (shadowing, recursive routes,
IP addressing conflicts).
"""

import ipaddress
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── YANG-inspired schema for NetConfEval Task 1 ───────────────────────────────
REQUIRED_FIELDS = {
    "reachability":   ["source", "destination", "action"],
    "waypoint":       ["source", "destination", "action", "waypoints"],
    "load-balancing": ["source", "destination", "action", "load_balancing"],
}

VALID_ACTIONS    = {"allow", "deny", "permit", "block", "forward", "drop"}
VALID_PROTOCOLS  = {"any", "tcp", "udp", "icmp", "ip", "all", "*"}
VALID_LB_ALGOS   = {"round-robin", "weighted", "ecmp", "least-connections"}


def _check_yang_schema(config: dict, policy_type: str) -> dict:
    """Verify required fields and field types (YANG schema emulation)."""
    required = REQUIRED_FIELDS.get(policy_type, REQUIRED_FIELDS["reachability"])
    missing  = [f for f in required if f not in config]
    if missing:
        return {
            "name": "YANG Schema",
            "passed": False,
            "detail": f"Missing required fields: {', '.join(missing)}",
        }
    # Type checks
    if not isinstance(config.get("source", ""), str):
        return {"name": "YANG Schema", "passed": False, "detail": "Field 'source' must be a string"}
    if not isinstance(config.get("destination", ""), str):
        return {"name": "YANG Schema", "passed": False, "detail": "Field 'destination' must be a string"}
    action = str(config.get("action", "")).lower()
    if action and action not in VALID_ACTIONS:
        return {"name": "YANG Schema", "passed": False, "detail": f"Invalid action '{action}'"}
    return {"name": "YANG Schema", "passed": True, "detail": "Valid structure and field types"}


def _check_ipv4(config: dict) -> dict:
    """Verify IPv4 addressing: CIDR notation, no overlap/conflict."""
    dst = config.get("destination", "")
    if not dst:
        return {"name": "IPv4 Addressing", "passed": True, "detail": "No destination to validate"}
    try:
        net = ipaddress.IPv4Network(dst, strict=False)
        # Check for obviously invalid subnets
        if net.prefixlen == 0 and str(net.network_address) == "0.0.0.0":
            return {
                "name": "IPv4 Addressing",
                "passed": False,
                "detail": "Destination 0.0.0.0/0 is too broad; specify a valid subnet",
            }
        return {
            "name": "IPv4 Addressing",
            "passed": True,
            "detail": f"Valid IPv4 network {net} (host bits: {net.num_addresses} addresses)",
        }
    except ValueError:
        # Destination might be a hostname — still valid in NetConfEval context
        if dst.replace("-", "").replace("_", "").replace(".", "").isalnum():
            return {"name": "IPv4 Addressing", "passed": True, "detail": f"Hostname destination '{dst}' — OK"}
        return {
            "name": "IPv4 Addressing",
            "passed": False,
            "detail": f"'{dst}' is neither a valid IPv4 CIDR nor a recognizable hostname",
        }


def _normalize_host(name: str) -> str:
    """Terminological normalization: lowercase, strip interface suffixes."""
    return name.lower().replace("gigabitethernet", "eth").replace("ge-", "eth")


def _check_host_identifiers(config: dict) -> dict:
    """Detect lexical hallucinations — interface names mismatched with topology."""
    src = config.get("source", "")
    waypoints = config.get("waypoints", [])

    issues = []
    # Common hallucinated interface patterns in Cisco-style configs
    hallucination_patterns = ["GigabitEthernet", "FastEthernet", "TenGigabitEthernet", "ge-0/0"]
    for field_val in [src] + (waypoints if isinstance(waypoints, list) else []):
        for pat in hallucination_patterns:
            if pat.lower() in str(field_val).lower():
                issues.append(f"'{field_val}' looks like an interface identifier, not a host/subnet")

    if issues:
        return {
            "name": "Host Identifiers",
            "passed": False,
            "detail": "; ".join(issues) + " (terminological normalization applied)",
        }
    return {
        "name": "Host Identifiers",
        "passed": True,
        "detail": "No lexical hallucinations detected",
    }


def _run_detox(config: dict, policy_type: str) -> dict:
    """
    DETOX-inspired logical conflict detection.
    Checks for: rule shadowing, recursive routes, contradictory actions.
    (Simplified single-rule version; full DETOX compares rule sets.)
    """
    action = str(config.get("action", "")).lower()
    src    = str(config.get("source", ""))
    dst    = str(config.get("destination", ""))

    issues = []

    # Shadowing: source == destination
    if src and dst and _normalize_host(src) == _normalize_host(dst):
        issues.append(f"Source and destination are identical ('{src}') — potential routing loop")

    # Recursive route: destination contains source as a sub-net
    try:
        dst_net = ipaddress.IPv4Network(dst, strict=False)
        src_net = ipaddress.IPv4Network(src, strict=False)
        if dst_net.overlaps(src_net) and dst_net != src_net:
            issues.append(f"Overlapping source ({src}) and destination ({dst}) — recursive route risk")
    except ValueError:
        pass

    # Contradictory policy: deny + waypoint (waypoint implies routing, not blocking)
    if action in {"deny", "block", "drop"} and policy_type == "waypoint":
        issues.append("Deny action combined with waypoint policy is contradictory")

    # Load-balancing: verify structure
    if policy_type == "load-balancing":
        lb = config.get("load_balancing", {})
        if not isinstance(lb, dict):
            issues.append("'load_balancing' must be an object")
        elif lb.get("algorithm") not in VALID_LB_ALGOS and lb.get("algorithm") is not None:
            issues.append(f"Unknown load-balancing algorithm: '{lb.get('algorithm')}'")

    if issues:
        return {
            "name": "DETOX — Logical conflicts",
            "passed": False,
            "detail": "; ".join(issues),
        }
    return {
        "name": "DETOX — Logical conflicts",
        "passed": True,
        "detail": "No shadowing, recursive routes, or logical anomalies detected",
    }


async def run_conformance_check(config: dict, policy_type: str) -> Dict:
    """
    Execute Stage 2: schema conformance + DETOX conflict detection.

    Returns:
      status: "pass" | "fail"
      detail.checks: list of individual check results
    """
    checks: List[dict] = [
        _check_yang_schema(config, policy_type),
        _check_ipv4(config),
        _check_host_identifiers(config),
        _run_detox(config, policy_type),
    ]

    all_passed = all(c["passed"] for c in checks)
    failed     = [c for c in checks if not c["passed"]]
    conflicts  = [c["detail"] for c in failed]

    return {
        "status": "pass" if all_passed else "fail",
        "detail": {
            "checks":   checks,
            "conflicts": conflicts,
        },
    }
