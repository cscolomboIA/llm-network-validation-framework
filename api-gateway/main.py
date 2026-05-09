"""
NetValidAI — API Gateway
FastAPI application that orchestrates the 4-stage validation pipeline.

Batch behavior (batch_size > 1):
  Stage 1: sends N intents together to the LLM → returns [JSON1...JSONN]
  Stages 2-4: run individually for each JSONi extracted from the batch,
              stopping at the first failed stage (preventive pipeline).
"""

import os
import asyncio
from typing import Optional, List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(
    title="NetValidAI Pipeline API",
    description="Framework for preventive verification and validation of LLM-generated network configurations.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from verifier.syntactic    import run_syntactic_check
from verifier.conformance  import run_conformance_check
from verifier.semantic     import run_semantic_check
from rag_engine.engine     import RAGEngine
from self_healing_agent.agent import SelfHealingAgent
from mininet_runner.orchestrator import run_mininet_validation

_rag: Optional[RAGEngine] = None

def get_rag() -> RAGEngine:
    global _rag
    if _rag is None:
        _rag = RAGEngine(os.getenv("NETCONFEVAL_PATH", "./rag-engine/data/netconfeval.json"))
    return _rag

AVAILABLE_MODELS = {
    "anthropic": [{"id": "claude-3-5-sonnet", "label": "Claude 3.5 Sonnet"}],
    "openai": [
        {"id": "gpt-4-azure",   "label": "GPT-4 Azure"},
        {"id": "gpt-4-turbo",   "label": "GPT-4 Turbo"},
        {"id": "gpt-3.5-turbo", "label": "GPT-3.5 Turbo"},
    ],
    "groq": [
        {"id": "llama-3.3-70b",  "label": "Llama 3.3 70B"},
        {"id": "llama-3.1-8b",   "label": "Llama 3.1 8B"},
        {"id": "mistral-large",  "label": "Mistral Large"},
        {"id": "deepseek-chat",  "label": "DeepSeek Chat"},
        {"id": "qwen-3",         "label": "Qwen-3"},
        {"id": "gemini-2.0-pro", "label": "Gemini 2.0 Pro"},
        {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
    ],
}

class PipelineRequest(BaseModel):
    intent: str
    model: str = "llama-3.3-70b"
    policy_type: str = "reachability"
    batch_size: int = 1
    enable_healing: bool = True

class IntentValidationRequest(BaseModel):
    intent: str


async def run_stages_234(
    intent: str,
    config: dict,
    model: str,
    policy_type: str,
    rag_ctx: dict,
    enable_healing: bool = False,
    is_primary: bool = False,
) -> dict:
    """
    Run Stages 2, 3 and 4 for a single JSON config extracted from the batch.
    Each stage only runs if the previous one passed (preventive pipeline).
    The config used is exactly what the LLM generated under batch stress.
    """
    agent = SelfHealingAgent(model=model, max_attempts=int(os.getenv("MAX_HEALING_ATTEMPTS", 3)))

    # Extract ground truth from RAG context
    examples = rag_ctx.get("examples", []) if rag_ctx else []
    ground_truth = examples[0].get("config") if examples else {}

    result = {
        "intent": intent,
        "is_primary": is_primary,
        "all_pass": False,
        "ground_truth": ground_truth,
        "stages": {
            "1_syntactic": {"status": "pass", "config": config},
        },
    }

    # Stage 2: conformance + DETOX
    s2 = await run_conformance_check(config=config, policy_type=policy_type)
    s3 = await run_semantic_check(config=config, policy_type=policy_type, rag_context=rag_ctx)
    s2["detail"] = {**s2.get("detail", {}), **s3.get("detail", {})}
    result["stages"]["2_conformance"] = s2

    if s2["status"] != "pass":
        # Self-healing only for primary intent
        if enable_healing and is_primary:
            healed = await agent.heal(
                intent=intent,
                failed_config=config,
                stage_error=s2,
                rag_context=rag_ctx,
                policy_type=policy_type,
            )
            if healed:
                result["stages"]["1_syntactic"]["config"] = healed
                config = healed
                # Re-run stage 2 with healed config
                s2 = await run_conformance_check(config=config, policy_type=policy_type)
                s3 = await run_semantic_check(config=config, policy_type=policy_type, rag_context=rag_ctx)
                s2["detail"] = {**s2.get("detail", {}), **s3.get("detail", {})}
                result["stages"]["2_conformance"] = s2
                if s2["status"] != "pass":
                    return result
            else:
                return result
        else:
            return result  # Stop here — stage 2 failed

    # Stage 4: Mininet
    s4 = await run_mininet_validation(
        config=config,
        policy_type=policy_type,
        mode=os.getenv("MININET_MODE", "simulated"),
    )
    result["stages"]["4_mininet"] = s4
    result["all_pass"] = s4["status"] == "pass"
    return result


@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0.0"}

@app.get("/api/models")
def list_models():
    return AVAILABLE_MODELS

@app.post("/api/validate/intent")
async def validate_intent(req: IntentValidationRequest):
    text = req.intent.strip()
    if len(text) < 10:
        return {"valid": False, "reason": "Intent too short."}
    keywords = ["reach", "connect", "traffic", "allow", "deny", "forward",
                "route", "subnet", "host", "network", "flow", "path"]
    if not any(k in text.lower() for k in keywords):
        return {"valid": False, "reason": "Intent does not appear to describe a network policy."}
    return {"valid": True}

@app.get("/api/dataset/samples")
async def dataset_samples(policy_type: str = "all", limit: int = 20, offset: int = 0):
    try:
        rag = get_rag()
        return rag.get_samples(policy_type=policy_type, limit=limit, offset=offset)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/pipeline/run")
async def pipeline_run(req: PipelineRequest):
    """
    4-stage preventive validation pipeline.

    Stage 1: if batch_size > 1, sends N different intents together to the LLM
             in a single prompt. The LLM returns a JSON array with N configs.
             Each config is what the LLM generated under context window stress.

    Stages 2-4: run individually for each extracted config.
                Each stage only executes if the previous one passed.
                This is the preventive pipeline described in the paper.

    batch_results: complete per-intent results for the batch report UI.
    """
    logger.info(f"Pipeline | model={req.model} policy={req.policy_type} batch={req.batch_size}")

    rag = get_rag()
    rag_ctx = rag.retrieve(intent=req.intent, policy_type=req.policy_type, k=int(os.getenv("RAG_TOP_K", 3)))

    # Fetch extra intents for batch
    extra_intents = []
    if req.batch_size > 1:
        samples = rag.get_samples(policy_type=req.policy_type, limit=req.batch_size + 5)
        extra_intents = [
            s["intent"] for s in samples.get("samples", [])
            if s["intent"].strip() != req.intent.strip()
        ][: req.batch_size - 1]

    all_intents = [req.intent] + extra_intents

    # ── Stage 1: single LLM call with N intents ───────────────────────────────
    s1_batch = await run_syntactic_check(
        intent=req.intent,
        model=req.model,
        policy_type=req.policy_type,
        batch_size=req.batch_size,
        rag_context=rag_ctx,
        extra_intents=extra_intents,
    )

    result = {
        "stages": {"1_syntactic": s1_batch},
        "all_pass": False,
        "rag_context": rag_ctx,
        "batch_results": [],
    }

    if s1_batch["status"] != "pass":
        logger.warning("Stage 1 FAIL — syntactic error in batch")
        return result

    # Extract all configs generated by the LLM under batch stress
    all_configs = s1_batch.get("all_configs", [s1_batch["config"]])
    # Ensure we have one config per intent
    while len(all_configs) < len(all_intents):
        all_configs.append({})

    # ── Stages 2-4 for primary intent (drives main UI panels) ────────────────
    primary_result = await run_stages_234(
        intent=req.intent,
        config=all_configs[0],
        model=req.model,
        policy_type=req.policy_type,
        rag_ctx=rag_ctx,
        enable_healing=req.enable_healing,
        is_primary=True,
    )
    result["stages"]["2_conformance"] = primary_result["stages"].get("2_conformance", {})
    result["stages"]["4_mininet"]     = primary_result["stages"].get("4_mininet", {})
    result["all_pass"]                = primary_result["all_pass"]

    # ── Stages 2-4 for all intents in batch (for batch report) ───────────────
    if req.batch_size > 1:
        logger.info(f"Running stages 2-4 for {len(all_intents)} batch intents...")
        batch_tasks = [
            run_stages_234(
                intent=all_intents[i],
                config=all_configs[i] if i < len(all_configs) else {},
                model=req.model,
                policy_type=req.policy_type,
                rag_ctx=rag.retrieve(intent=all_intents[i], policy_type=req.policy_type, k=1),
                enable_healing=False,
                is_primary=(i == 0),
            )
            for i in range(len(all_intents))
        ]
        result["batch_results"] = await asyncio.gather(*batch_tasks)
        passed = sum(1 for r in result["batch_results"] if r["all_pass"])
        logger.info(f"Batch done | {passed}/{len(all_intents)} passed")

    return result
