"""
NetValidAI — Verifier Service
Etapa 2: Verificação de Conformidade (schema YANG, endereçamento IPv4)
Etapa 3: Verificação Semântica (ground truth + DETOX)
"""

import ipaddress
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from detox.detector import DetoxDetector
from yang.validator import YANGValidator

logging.basicConfig(level="INFO")
logger = logging.getLogger("verifier")

app = FastAPI(title="NetValidAI Verifier", version="2.0.0")

yang_validator = YANGValidator(schema_dir=Path("/app/yang"))
detox = DetoxDetector()


class VerifyRequest(BaseModel):
    config: dict | list
    intent: str
    policy_type: str = "reachability"
    ground_truth: dict | None = None   # opcional: vem do RAG


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str
    stage: int   # 2 = conformidade, 3 = semântica


class VerifyResponse(BaseModel):
    all_pass: bool
    checks: list[CheckResult]
    similarity_score: float
    conflicts: list[str]
    error_classification: str | None   # para o self-healing agent


@app.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest):
    checks: list[CheckResult] = []
    # Detecção de batch dropout (fenômeno identificado no artigo SBRC 2026)
    batch_ok, batch_detail = validate_batch_redundancy(req.config)
    checks.append(CheckResult(
        name="Batch Dropout",
        passed=batch_ok,
        detail=batch_detail,
        stage=2,
    ))
    conflicts: list[str] = []

    # =========================================================================
    # ETAPA 2A — Schema YANG
    # =========================================================================
    yang_ok, yang_detail = yang_validator.validate(req.config)
    checks.append(CheckResult(
        name="Schema YANG",
        passed=yang_ok,
        detail=yang_detail,
        stage=2,
    ))

    # =========================================================================
    # ETAPA 2B — Endereçamento IPv4
    # =========================================================================
    cfg = req.config[0] if isinstance(req.config, list) else req.config
    ip_ok, ip_detail = validate_ipv4(cfg)
    checks.append(CheckResult(
        name="Endereçamento IPv4",
        passed=ip_ok,
        detail=ip_detail,
        stage=2,
    ))

    # =========================================================================
    # ETAPA 2C — Identificadores de host / normalização terminológica
    # Detecta "alucinação léxica" — ex: GigabitEthernet em vez de eth0
    # (fenômeno observado no Claude 3.5 Sonnet no artigo)
    # =========================================================================
    norm_ok, norm_detail = validate_identifiers(cfg)
    checks.append(CheckResult(
        name="Identificadores de host",
        passed=norm_ok,
        detail=norm_detail,
        stage=2,
    ))

    # =========================================================================
    # ETAPA 3A — Detecção de inconsistências via DETOX
    # Jesus et al. 2016 — shadowing, redundância, correlação
    # =========================================================================
    detected_conflicts = detox.analyze(req.config, req.policy_type)
    conflicts.extend(detected_conflicts)
    detox_ok = len(detected_conflicts) == 0
    checks.append(CheckResult(
        name="DETOX — conflitos lógicos",
        passed=detox_ok,
        detail=f"{len(detected_conflicts)} conflito(s) detectado(s): {detected_conflicts}" if detected_conflicts else "Sem conflitos",
        stage=3,
    ))

    # =========================================================================
    # ETAPA 3B — Similaridade com ground truth (se disponível via RAG)
    # =========================================================================
    similarity = 0.0
    if req.ground_truth:
        similarity = compute_similarity(req.config, req.ground_truth)
        sim_ok = similarity >= 0.6
        checks.append(CheckResult(
            name="Aderência ao ground truth",
            passed=sim_ok,
            detail=f"Similaridade: {similarity:.2%}",
            stage=3,
        ))

    # =========================================================================
    # Classificação do erro — alimenta o self-healing agent
    # =========================================================================
    error_classification = None
    if not all(c.passed for c in checks):
        error_classification = classify_error(checks, conflicts)

    all_pass = all(c.passed for c in checks)
    return VerifyResponse(
        all_pass=all_pass,
        checks=checks,
        similarity_score=similarity,
        conflicts=conflicts,
        error_classification=error_classification,
    )


# =============================================================================
# Helpers de validação
# =============================================================================

def validate_batch_redundancy(config: dict | list) -> tuple[bool, str]:
    """Detecta batch dropout por duplicação — Llama 3.3 70B com batch > 20."""
    if not isinstance(config, list):
        return True, "Config única — sem batch"
    unique = set(json.dumps(c, sort_keys=True) for c in config)
    if len(unique) == 1:
        return False, f"BATCH DROPOUT: {len(config)} regras idênticas geradas — modelo colapsou"
    if len(unique) < len(config) * 0.5:
        return False, f"BATCH DROPOUT parcial: {len(config)} regras, apenas {len(unique)} únicas"
    return True, f"Batch diverso: {len(unique)} regras únicas de {len(config)}"

def validate_ipv4(config: dict | list) -> tuple[bool, str]:
    """Valida prefixos IPv4 e detecta conflitos de endereçamento."""
    dest = config.get("destination", "")
    if not dest:
        return False, "Campo 'destination' ausente"
    try:
        net = ipaddress.ip_network(dest, strict=False)
        # Verifica se não é endereço de loopback ou reservado para uso especial
        if net.is_loopback:
            return False, f"Destino {dest} é endereço de loopback"
        return True, f"Prefixo {dest} válido ({net.num_addresses} endereços)"
    except ValueError as e:
        return False, f"Prefixo inválido '{dest}': {e}"


def validate_identifiers(config: dict | list) -> tuple[bool, str]:
    """
    Normalização terminológica — detecta interface names incorretas.
    No artigo: Claude 3.5 Sonnet gerava 'GigabitEthernet' em ambientes
    que esperavam 'eth0' — alucinação léxica.
    """
    suspect_patterns = ["GigabitEthernet", "FastEthernet", "TenGigabitEthernet"]
    config_str = json.dumps(config)
    found = [p for p in suspect_patterns if p in config_str]
    if found:
        return False, f"Identificadores Cisco IOS em ambiente Linux: {found}. Use eth0, eth1..."
    return True, "Identificadores de interface normalizados"


def compute_similarity(config: dict | list, ground_truth: dict) -> float:
    """
    Similaridade estrutural token-a-token entre config gerada e ground truth.
    Reproduz a métrica da Figura 4 (ECDF) do artigo.
    """
    config_tokens = set(json.dumps(config, sort_keys=True).split())
    truth_tokens  = set(json.dumps(ground_truth, sort_keys=True).split())
    if not truth_tokens:
        return 0.0
    intersection = config_tokens & truth_tokens
    return len(intersection) / len(truth_tokens)


def classify_error(checks: list[CheckResult], conflicts: list[str]) -> str:
    """
    Classifica o tipo de erro para o self-healing agent direcionar
    a estratégia de correção correta.
    """
    failed = [c for c in checks if not c.passed]
    if any("JSON" in c.name or "sintáti" in c.detail.lower() for c in failed):
        return "syntactic"
    if any("IPv4" in c.name or "identificador" in c.name.lower() for c in failed):
        return "terminological"
    if conflicts or any(c.stage == 3 for c in failed):
        return "logical"
    return "unknown"


@app.get("/health")
async def health():
    return {"status": "ok", "service": "verifier"}
