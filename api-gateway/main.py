"""
NetValidAI — API Gateway
FastAPI application that orchestrates the 4-stage validation pipeline.
"""

import os
import json
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

# ── Import pipeline modules ──────────────────────────────────────────────────
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from verifier.syntactic    import run_syntactic_check
from verifier.conformance  import run_conformance_check
from verifier.semantic     import run_semantic_check
from rag_engine.engine     import RAGEngine
from self_healing_agent.agent import SelfHealingAgent
from mininet_runner.orchestrator import run_mininet_validation

# ── Lazy-loaded singletons ───────────────────────────────────────────────────
_rag: Optional[RAGEngine] = None

def get_rag() -> RAGEngine:
    global _rag
    if _rag is None:
        _rag = RAGEngine(os.getenv("NETCONFEVAL_PATH", "./rag-engine/data/netconfeval.json"))
    return _rag

# ── Models ───────────────────────────────────────────────────────────────────
AVAILABLE_MODELS = {
    "anthropic": [
        {"id": "claude-3-5-sonnet", "label": "Claude 3.5 Sonnet"},
    ],
    "openai": [
        {"id": "gpt-4-azure",  "label": "GPT-4 Azure"},
        {"id": "gpt-4-turbo",  "label": "GPT-4 Turbo"},
        {"id": "gpt-3.5-turbo","label": "GPT-3.5 Turbo"},
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

# ── Request / Response schemas ────────────────────────────────────────────────
class PipelineRequest(BaseModel):
    intent: str
    model: str = "llama-3.3-70b"
    policy_type: str = "reachability"
    batch_size: int = 1
    enable_healing: bool = True

class IntentValidationRequest(BaseModel):
    intent: str

# ── Helper: run full pipeline for a single intent ─────────────────────────────
async def run_single_pipeline(
    intent: str,
    model: str,
    policy_type: str,
    rag_ctx: dict,
    enable_healing: bool = True,
    is_primary: bool = False,
) -> dict:
    """Run all 4 stages for a single intent and return the full result."""
    agent = SelfHealingAgent(model=model, max_attempts=int(os.getenv("MAX_HEALING_ATTEMPTS", 3)))

    # Stage 1
    s1 = await run_syntactic_check(
        intent=intent,
        model=model,
        policy_type=policy_type,
        batch_size=1,  # individual run — no batch stress
        rag_context=rag_ctx,
        extra_intents=None,
    )

    result = {
        "intent": intent,
        "is_primary": is_primary,
        "all_pass": False,
        "stages": {"1_syntactic": s1},
    }

    if s1["status"] != "pass":
        return result

    # Stage 2 + 3
    s2 = await run_conformance_check(config=s1["config"], policy_type=policy_type)
    s3 = await run_semantic_check(config=s1["config"], policy_type=policy_type, rag_context=rag_ctx)
    s2["detail"] = {**s2.get("detail", {}), **s3.get("detail", {})}
    result["stages"]["2_conformance"] = s2

    if s2["status"] != "pass":
        if enable_healing and is_primary:
            healed = await agent.heal(
                intent=intent,
                failed_config=s1["config"],
                stage_error=s2,
                rag_context=rag_ctx,
                policy_type=policy_type,
            )
            if healed:
                result["stages"]["1_syntactic"]["config"] = healed
                s1["config"] = healed
            else:
                return result
        else:
            return result

    # Stage 4
    s4 = await run_mininet_validation(
        config=s1["config"],
        policy_type=policy_type,
        mode=os.getenv("MININET_MODE", "simulated"),
    )
    result["stages"]["4_mininet"] = s4
    result["all_pass"] = (
        s1["status"] == "pass"
        and s2["status"] == "pass"
        and s4["status"] == "pass"
    )
    return result

# ── Endpoints ─────────────────────────────────────────────────────────────────
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
    has_kw = any(k in text.lower() for k in keywords)
    if not has_kw:
        return {"valid": False, "reason": "Intent does not appear to describe a network policy."}
    return {"valid": True}


@app.get("/api/dataset/samples")
async def dataset_samples(policy_type: str = "all", limit: int = 20, offset: int = 0):
    try:
        rag = get_rag()
        samples = rag.get_samples(policy_type=policy_type, limit=limit, offset=offset)
        return samples
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/pipeline/run")
async def pipeline_run(req: PipelineRequest):
    """
    Execute the 4-stage preventive validation pipeline.

    For batch_size > 1:
    - Sends N different intents simultaneously to the LLM (context window stress)
    - Then runs the full 4-stage pipeline individually for each intent
    - Returns batch_results with complete per-intent results for the report
    """
    logger.info(f"Pipeline start | model={req.model} policy={req.policy_type} batch={req.batch_size}")

    rag = get_rag()

    # RAG context for primary intent
    rag_ctx = rag.retrieve(
        intent=req.intent,
        policy_type=req.policy_type,
        k=int(os.getenv("RAG_TOP_K", 3)),
    )

    # Fetch extra intents for batch
    extra_intents = []
    if req.batch_size > 1:
        samples = rag.get_samples(
            policy_type=req.policy_type,
            limit=req.batch_size + 5,
        )
        extra_intents = [
            s["intent"] for s in samples.get("samples", [])
            if s["intent"].strip() != req.intent.strip()
        ][: req.batch_size - 1]

    all_intents = [req.intent] + extra_intents

    # ── Stage 1: Batch syntactic check (all N intents in one prompt) ──────────
    s1_batch = await run_syntactic_check(
        intent=req.intent,
        model=req.model,
        policy_type=req.policy_type,
        batch_size=req.batch_size,
        rag_context=rag_ctx,
        extra_intents=extra_intents,
    )

    # Primary result (intent #1) — drives the main pipeline panels
    result = {
        "stages": {},
        "all_pass": False,
        "rag_context": rag_ctx,
        "batch_results": [],
    }

    result["stages"]["1_syntactic"] = s1_batch

    if s1_batch["status"] != "pass":
        logger.warning("Stage 1 FAIL — syntactic error")
        return result

    # ── Stages 2-4 for primary intent ────────────────────────────────────────
    agent = SelfHealingAgent(model=req.model, max_attempts=int(os.getenv("MAX_HEALING_ATTEMPTS", 3)))

    s2 = await run_conformance_check(config=s1_batch["config"], policy_type=req.policy_type)
    s3 = await run_semantic_check(config=s1_batch["config"], policy_type=req.policy_type, rag_context=rag_ctx)
    s2["detail"] = {**s2.get("detail", {}), **s3.get("detail", {})}
    result["stages"]["2_conformance"] = s2

    if s2["status"] != "pass":
        if req.enable_healing:
            healed = await agent.heal(
                intent=req.intent,
                failed_config=s1_batch["config"],
                stage_error=s2,
                rag_context=rag_ctx,
                policy_type=req.policy_type,
            )
            if healed:
                result["stages"]["1_syntactic"]["config"] = healed
                s1_batch["config"] = healed
        else:
            # Still run batch results even if primary fails
            pass

    s4 = await run_mininet_validation(
        config=s1_batch["config"],
        policy_type=req.policy_type,
        mode=os.getenv("MININET_MODE", "simulated"),
    )
    result["stages"]["4_mininet"] = s4

    result["all_pass"] = (
        s1_batch["status"] == "pass"
        and s2["status"] == "pass"
        and s4["status"] == "pass"
    )

    # ── Batch: run full pipeline individually for each intent ─────────────────
    if req.batch_size > 1:
        logger.info(f"Running individual pipeline for {len(all_intents)} intents...")
        batch_tasks = [
            run_single_pipeline(
                intent=intent_i,
                model=req.model,
                policy_type=req.policy_type,
                rag_ctx=rag.retrieve(intent=intent_i, policy_type=req.policy_type, k=1),
                enable_healing=False,  # no healing for secondary intents
                is_primary=(i == 0),
            )
            for i, intent_i in enumerate(all_intents)
        ]
        result["batch_results"] = await asyncio.gather(*batch_tasks)
        logger.info(f"Batch complete | {sum(1 for r in result['batch_results'] if r['all_pass'])}/{len(all_intents)} passed")

    logger.info(f"Pipeline end | all_pass={result['all_pass']}")
    return result
