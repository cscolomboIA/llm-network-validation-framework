#!/usr/bin/env python3
"""
run_benchmark.py — Reproduce the paper's benchmark experiments
==============================================================
Runs the full 4-stage pipeline over the NetConfEval Task 1 dataset
for multiple models, policy types, and batch sizes.

Usage:
  python scripts/run_benchmark.py \\
    --models claude-3-5-sonnet gpt-4-azure llama-3.3-70b \\
    --policy_types reachability waypoint load-balancing \\
    --batch_sizes 1 3 5 9 20 33 50 100 \\
    --output results/benchmark_run.json
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rag_engine.engine     import RAGEngine
from verifier.syntactic    import run_syntactic_check
from verifier.conformance  import run_conformance_check
from verifier.semantic     import run_semantic_check
from mininet_runner.orchestrator import run_mininet_validation

NETCONFEVAL_PATH = os.getenv("NETCONFEVAL_PATH", "./rag-engine/data/netconfeval.json")
MININET_MODE     = os.getenv("MININET_MODE", "simulated")


async def run_single(rag, intent, model, policy_type, batch_size):
    """Run one intent through the full pipeline and return stage results."""
    rag_ctx = rag.retrieve(intent, policy_type=policy_type, k=3)
    t0 = time.monotonic()

    s1 = await run_syntactic_check(intent, model, policy_type, batch_size, rag_ctx)
    if s1["status"] != "pass":
        return {"all_pass": False, "stages": {"1_syntactic": s1}, "elapsed_s": time.monotonic()-t0}

    s2 = await run_conformance_check(s1["config"], policy_type)
    s3 = await run_semantic_check(s1["config"], policy_type, rag_ctx)
    s2["detail"] = {**s2.get("detail", {}), **s3.get("detail", {})}

    if s2["status"] != "pass":
        return {"all_pass": False, "stages": {"1_syntactic": s1, "2_conformance": s2},
                "elapsed_s": time.monotonic()-t0}

    s4 = await run_mininet_validation(s1["config"], policy_type, mode=MININET_MODE)
    return {
        "all_pass": s1["status"]=="pass" and s2["status"]=="pass" and s4["status"]=="pass",
        "stages":  {"1_syntactic": s1, "2_conformance": s2, "4_mininet": s4},
        "elapsed_s": time.monotonic()-t0,
    }


async def main(args):
    rag = RAGEngine(NETCONFEVAL_PATH)
    results = {"run_at": datetime.utcnow().isoformat(), "args": vars(args), "data": {}}

    for model in args.models:
        results["data"][model] = {}
        for policy in args.policy_types:
            results["data"][model][policy] = {}
            # Get samples for this policy
            sample_data = rag.get_samples(policy_type=policy, limit=args.samples_per_cell)
            samples = sample_data["samples"]
            if not samples:
                print(f"  No samples for policy={policy}")
                continue

            for batch in args.batch_sizes:
                print(f"  {model} | {policy} | batch={batch} — running {len(samples)} samples")
                passed, total = 0, 0
                stage_pass = {1: 0, 2: 0, 4: 0}

                for s in samples:
                    res = await run_single(rag, s["intent"], model, policy, batch)
                    total += 1
                    if res["stages"].get("1_syntactic", {}).get("status") == "pass":
                        stage_pass[1] += 1
                    if res["stages"].get("2_conformance", {}).get("status") == "pass":
                        stage_pass[2] += 1
                    if res["stages"].get("4_mininet", {}).get("status") == "pass":
                        stage_pass[4] += 1
                    if res["all_pass"]:
                        passed += 1
                    # Rate-limit
                    await asyncio.sleep(args.delay)

                results["data"][model][policy][str(batch)] = {
                    "total":        total,
                    "all_pass":     passed,
                    "syntactic_acc": round(stage_pass[1] / total, 4) if total else 0,
                    "conformance_acc": round(stage_pass[2] / total, 4) if total else 0,
                    "operational_acc": round(stage_pass[4] / total, 4) if total else 0,
                }
                print(f"    syntactic={stage_pass[1]}/{total} conformance={stage_pass[2]}/{total} mininet={stage_pass[4]}/{total}")

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="NetValidAI benchmark runner")
    p.add_argument("--models",          nargs="+", default=["llama-3.3-70b"],
                   help="LLM model IDs to evaluate")
    p.add_argument("--policy_types",    nargs="+", default=["reachability","waypoint","load-balancing"],
                   help="Policy types from NetConfEval")
    p.add_argument("--batch_sizes",     nargs="+", type=int, default=[1,3,5,9,20,33,50,100],
                   help="Batch sizes to test (context window stress)")
    p.add_argument("--samples_per_cell",type=int,  default=10,
                   help="Number of intents to evaluate per (model, policy, batch) cell")
    p.add_argument("--delay",           type=float, default=0.5,
                   help="Seconds between API calls (rate-limiting)")
    p.add_argument("--output",          default="results/benchmark.json",
                   help="Output file path")
    args = p.parse_args()
    asyncio.run(main(args))
