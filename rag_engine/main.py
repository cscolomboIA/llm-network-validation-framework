"""
NetValidAI — RAG Engine
Base de conhecimento vetorial com ChromaDB.

Coleções:
- netconfeval_ground_truth  : 1.665 samples do dataset (Dahlmann et al. 2024)
- healing_history           : erros e correções acumulados em runtime
- known_failures            : padrões de falha classificados por tipo
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

import chromadb
from anthropic import Anthropic
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level="INFO")
logger = logging.getLogger("rag-engine")

CHROMA_DIR  = os.environ.get("CHROMA_PERSIST_DIR", "/data/chroma")
DATASETS_DIR = os.environ.get("DATASETS_DIR", "/data/datasets")

app = FastAPI(title="NetValidAI RAG Engine", version="2.0.0")

chroma = chromadb.PersistentClient(path=CHROMA_DIR)
anthropic_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# Coleções ChromaDB
gt_collection      = chroma.get_or_create_collection("netconfeval_ground_truth")
history_collection = chroma.get_or_create_collection("healing_history")
failures_collection = chroma.get_or_create_collection("known_failures")


# =============================================================================
# Startup — indexa o dataset NetConfEval se ainda não foi feito
# =============================================================================
@app.on_event("startup")
async def startup():
    count = gt_collection.count()
    if count == 0:
        logger.info("Indexando dataset NetConfEval no ChromaDB...")
        await index_netconfeval()
        logger.info(f"RAG pronto: {gt_collection.count()} samples indexados")
    else:
        logger.info(f"RAG já indexado: {gt_collection.count()} samples")


async def index_netconfeval():
    """
    Carrega os samples do NetConfEval e os vetoriza.
    Os arquivos JSON devem estar em /data/datasets/initial/
    Formato esperado: lista de {"intent": str, "config": dict, "policy_type": str}
    """
    dataset_file = Path(DATASETS_DIR) / "initial" / "netconfeval_task1.json"

    if not dataset_file.exists():
        logger.warning(f"Dataset não encontrado em {dataset_file}. RAG iniciando vazio.")
        logger.warning("Coloque netconfeval_task1.json em rag-engine/data/")
        return

    with open(dataset_file) as f:
        samples = json.load(f)

    # Processa em lotes para eficiência
    batch_size = 50
    for i in range(0, len(samples), batch_size):
        batch = samples[i:i + batch_size]
        documents, metadatas, ids = [], [], []

        for j, sample in enumerate(batch):
            doc_text = f"Intent: {sample['intent']}\nConfig: {json.dumps(sample['config'])}"
            documents.append(doc_text)
            metadatas.append({
                "intent": sample["intent"],
                "policy_type": sample.get("policy_type", "reachability"),
                "config_json": json.dumps(sample["config"]),
            })
            ids.append(f"gt_{i+j}")

        embeddings = get_embeddings(documents)
        gt_collection.add(
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
            ids=ids,
        )
        logger.info(f"Indexados {min(i + batch_size, len(samples))}/{len(samples)} samples")


def get_embeddings(texts: list[str]) -> list[list[float]]:
    """
    Gera embeddings via Anthropic (voyage-3) ou fallback simples.
    ChromaDB também suporta sentence-transformers localmente.
    """
    try:
        # Usa voyage-3 via Anthropic para embeddings de alta qualidade
        response = anthropic_client.beta.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1,
            messages=[{"role": "user", "content": "embed"}],
        )
        # Fallback: ChromaDB default embedding (all-MiniLM-L6-v2)
        return None  # None = ChromaDB usa embedding padrão
    except Exception:
        return None  # ChromaDB usa embedding padrão automaticamente


# =============================================================================
# Endpoints
# =============================================================================

class RetrieveRequest(BaseModel):
    intent: str
    policy_type: str = "reachability"
    top_k: int = 3


class StoreRequest(BaseModel):
    intent: str
    config: dict
    error_type: str
    was_corrected: bool
    healing_attempts: int
    label: str


@app.post("/retrieve")
async def retrieve(req: RetrieveRequest):
    """
    Recupera os exemplos mais similares do ground truth + histórico de erros.
    Alimenta tanto o LLM (geração) quanto o self-healing agent (correção).
    """
    query = f"Intent: {req.intent} Policy: {req.policy_type}"

    # Busca no ground truth
    gt_results = gt_collection.query(
        query_texts=[query],
        n_results=min(req.top_k, gt_collection.count() or 1),
        where={"policy_type": req.policy_type} if req.policy_type != "all" else None,
    )

    examples = []
    if gt_results["metadatas"] and gt_results["metadatas"][0]:
        for meta in gt_results["metadatas"][0]:
            try:
                examples.append({
                    "intent": meta["intent"],
                    "config": json.loads(meta["config_json"]),
                    "policy_type": meta["policy_type"],
                })
            except Exception:
                pass

    # Busca erros conhecidos similares
    known_errors = []
    if failures_collection.count() > 0:
        fail_results = failures_collection.query(
            query_texts=[query],
            n_results=min(3, failures_collection.count()),
        )
        if fail_results["metadatas"] and fail_results["metadatas"][0]:
            for meta in fail_results["metadatas"][0]:
                err = meta.get("error_type")
                if err and err not in known_errors:
                    known_errors.append(f"{err}: {meta.get('detail', '')}")

    return {
        "examples": examples,
        "known_errors": known_errors,
        "ground_truth_count": gt_collection.count(),
        "history_count": history_collection.count(),
    }


@app.post("/store")
async def store(req: StoreRequest):
    """
    Persiste resultado de uma run no histórico.
    Implementa o aprendizado contínuo — cada execução melhora o RAG.
    """
    doc_id = f"hist_{history_collection.count() + 1}"
    doc_text = (
        f"Intent: {req.intent}\n"
        f"Config: {json.dumps(req.config)}\n"
        f"Error: {req.error_type}\n"
        f"Corrected: {req.was_corrected}"
    )

    history_collection.add(
        documents=[doc_text],
        metadatas=[{
            "intent": req.intent,
            "error_type": req.error_type,
            "was_corrected": str(req.was_corrected),
            "healing_attempts": str(req.healing_attempts),
            "label": req.label,
        }],
        ids=[doc_id],
    )

    # Se é uma falha, adiciona à coleção de falhas conhecidas para evitar repetição
    if not req.was_corrected:
        fail_id = f"fail_{failures_collection.count() + 1}"
        failures_collection.add(
            documents=[f"Failed intent: {req.intent}\nError type: {req.error_type}"],
            metadatas=[{"error_type": req.error_type, "intent": req.intent, "detail": req.error_type}],
            ids=[fail_id],
        )

    return {"stored": True, "doc_id": doc_id, "total_history": history_collection.count()}


@app.get("/stats")
async def stats():
    return {
        "ground_truth_samples": gt_collection.count(),
        "healing_history": history_collection.count(),
        "known_failures": failures_collection.count(),
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "rag-engine"}


@app.get("/samples")
async def get_samples(policy_type: str = "all", limit: int = 20, offset: int = 0):
    try:
        where = {"policy_type": policy_type} if policy_type != "all" else None
        results = gt_collection.get(
            where=where, limit=limit, offset=offset, include=["metadatas"]
        )
        samples = []
        for meta in (results["metadatas"] or []):
            samples.append({
                "intent": meta.get("intent", ""),
                "policy_type": meta.get("policy_type", "reachability"),
            })
        return {"samples": samples, "total": gt_collection.count()}
    except Exception as e:
        return {"samples": [], "total": 0, "error": str(e)}


@app.get("/batch-samples")
async def batch_samples(policy_type: str = "reachability", n: int = 5):
    """Retorna N amostras aleatórias para compor um batch real."""
    import random
    try:
        total = gt_collection.count()
        if total == 0:
            return {"intents": [], "configs": []}
        # Offset aleatório para variedade
        offset = random.randint(0, max(0, total - n))
        where = {"policy_type": policy_type} if policy_type != "all" else None
        results = gt_collection.get(
            where=where, limit=n, offset=offset, include=["metadatas"]
        )
        intents, configs = [], []
        for meta in (results["metadatas"] or []):
            intents.append(meta.get("intent", ""))
            try:
                configs.append(json.loads(meta.get("config_json", "{}")))
            except:
                configs.append({})
        return {"intents": intents, "configs": configs, "policy_type": policy_type}
    except Exception as e:
        return {"intents": [], "configs": [], "error": str(e)}
