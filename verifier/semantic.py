"""
Stage 3 — Semantic Verification
---------------------------------
Compares the generated configuration against the NetConfEval ground truth
retrieved via RAG, computing a similarity score and checking semantic
consistency of network reachability requirements.
"""

import json
import logging
import math
from typing import Dict

logger = logging.getLogger(__name__)


def _flatten(obj, prefix="") -> dict:
    """Flatten a nested dict into dot-notation key-value pairs."""
    items = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            items.update(_flatten(v, prefix + k + "."))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            items.update(_flatten(v, prefix + str(i) + "."))
    else:
        items[prefix.rstrip(".")] = str(obj).lower().strip()
    return items


def _token_overlap_similarity(a: dict, b: dict) -> float:
    """
    Compute similarity between two configs based on token overlap of their
    flattened key-value pairs. Critical fields (source, destination, action)
    are weighted 3×.
    """
    CRITICAL = {"source", "destination", "action"}
    fa, fb = _flatten(a), _flatten(b)

    score, total = 0.0, 0.0
    all_keys = set(fa) | set(fb)

    for key in all_keys:
        weight = 3.0 if any(c in key for c in CRITICAL) else 1.0
        total += weight
        va = fa.get(key, "")
        vb = fb.get(key, "")
        if va == vb:
            score += weight
        elif va and vb:
            # Partial token overlap
            ta, tb = set(va.split()), set(vb.split())
            if ta | tb:
                score += weight * len(ta & tb) / len(ta | tb)

    return round(score / total, 4) if total > 0 else 0.0


def _check_reachability_semantics(config: dict, intent: str) -> dict:
    """
    Verify that the config semantically satisfies the intent:
    - source host appears in intent
    - destination subnet appears in intent
    - action is 'allow' for positive reachability policies
    """
    intent_lower = intent.lower()
    src = str(config.get("source", "")).lower()
    dst = str(config.get("destination", "")).lower()
    action = str(config.get("action", "")).lower()

    issues = []

    if src and src not in intent_lower:
        # Try sub-match (e.g. "lyon" in "from lyon can reach")
        if not any(src in word for word in intent_lower.split()):
            issues.append(f"Source '{src}' not found in the original intent")

    if dst:
        dst_base = dst.split("/")[0]
        if dst_base not in intent_lower and dst not in intent_lower:
            issues.append(f"Destination '{dst}' not found in the original intent")

    reach_keywords = ["reach", "connect", "allow", "forward", "access", "communicate"]
    deny_keywords  = ["deny", "block", "drop", "forbid", "prevent", "isolate"]
    is_positive    = any(k in intent_lower for k in reach_keywords)
    is_negative    = any(k in intent_lower for k in deny_keywords)

    if is_positive and not is_negative and action in {"deny", "block", "drop"}:
        issues.append(
            f"Policy action '{action}' contradicts a positive reachability intent"
        )

    return {
        "semantic_issues": issues,
        "intent_aligned": len(issues) == 0,
    }


async def run_semantic_check(config: dict, policy_type: str, rag_context: dict) -> Dict:
    """
    Execute Stage 3: RAG-assisted semantic analysis.

    Returns:
      status: "pass" | "fail"
      detail.similarity_score: float 0–1
      detail.semantic_issues:  list of identified issues
      detail.ground_truth:     the reference config (from RAG)
    """
    examples = rag_context.get("examples", []) if rag_context else []
    ground_truth = examples[0].get("config") if examples else None
    intent_text  = examples[0].get("intent", "") if examples else ""

    similarity = 0.0
    if ground_truth:
        similarity = _token_overlap_similarity(config, ground_truth)

    sem = _check_reachability_semantics(config, intent_text or str(config))

    passed = similarity >= 0.5 and sem["intent_aligned"]

    result = {
        "status": "pass" if passed else "fail",
        "detail": {
            "similarity_score":  similarity,
            "semantic_issues":   sem["semantic_issues"],
            "intent_aligned":    sem["intent_aligned"],
            "ground_truth":      ground_truth,
        },
    }

    if not passed:
        reasons = []
        if similarity < 0.5:
            reasons.append(f"Low similarity to ground truth ({similarity:.1%})")
        reasons.extend(sem["semantic_issues"])
        result["detail"]["failure_reasons"] = reasons

    return result
