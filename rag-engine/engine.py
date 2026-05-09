"""
RAG Engine — NetConfEval Ground Truth Retrieval
------------------------------------------------
Implements retrieval-augmented generation support by indexing the NetConfEval
dataset and retrieving the most similar examples for a given user intent.

Similarity is computed via TF-IDF + cosine similarity over intent text.
"""

import json
import os
import logging
import math
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Embedded minimal dataset (fallback when no file is present) ───────────────
# These 20 samples cover all 3 policy types and serve as a demo dataset.
BUILTIN_SAMPLES = [
    {"intent": "Traffic originating from lyon can reach the subnet 100.0.8.0/24.",
     "policy_type": "reachability",
     "config": {"source": "lyon", "destination": "100.0.8.0/24", "action": "allow", "protocol": "any", "priority": 100}},
    {"intent": "Traffic from paris must not reach the subnet 192.168.1.0/24.",
     "policy_type": "reachability",
     "config": {"source": "paris", "destination": "192.168.1.0/24", "action": "deny", "protocol": "any", "priority": 90}},
    {"intent": "Hosts in subnet 10.0.1.0/24 can reach the server at 172.16.0.5/32.",
     "policy_type": "reachability",
     "config": {"source": "10.0.1.0/24", "destination": "172.16.0.5/32", "action": "allow", "protocol": "tcp", "priority": 110}},
    {"intent": "All traffic from berlin must be able to communicate with 10.10.10.0/24.",
     "policy_type": "reachability",
     "config": {"source": "berlin", "destination": "10.10.10.0/24", "action": "allow", "protocol": "any", "priority": 100}},
    {"intent": "Traffic from madrid is denied access to subnet 203.0.113.0/24.",
     "policy_type": "reachability",
     "config": {"source": "madrid", "destination": "203.0.113.0/24", "action": "deny", "protocol": "any", "priority": 80}},
    {"intent": "Traffic from amsterdam to 10.0.5.0/24 must pass through firewall1.",
     "policy_type": "waypoint",
     "config": {"source": "amsterdam", "destination": "10.0.5.0/24", "action": "allow", "protocol": "any", "priority": 100, "waypoints": ["firewall1"]}},
    {"intent": "Packets from rome going to 192.168.100.0/24 must traverse ids-sensor.",
     "policy_type": "waypoint",
     "config": {"source": "rome", "destination": "192.168.100.0/24", "action": "allow", "protocol": "any", "priority": 100, "waypoints": ["ids-sensor"]}},
    {"intent": "All traffic from london to 172.20.0.0/16 should go via proxy-node and monitor-box.",
     "policy_type": "waypoint",
     "config": {"source": "london", "destination": "172.20.0.0/16", "action": "allow", "protocol": "any", "priority": 100, "waypoints": ["proxy-node", "monitor-box"]}},
    {"intent": "Traffic from tokyo must reach 10.1.0.0/24 only through edge-router.",
     "policy_type": "waypoint",
     "config": {"source": "tokyo", "destination": "10.1.0.0/24", "action": "allow", "protocol": "any", "priority": 100, "waypoints": ["edge-router"]}},
    {"intent": "Connections from oslo to 10.50.0.0/16 must pass through vpn-gateway.",
     "policy_type": "waypoint",
     "config": {"source": "oslo", "destination": "10.50.0.0/16", "action": "allow", "protocol": "any", "priority": 100, "waypoints": ["vpn-gateway"]}},
    {"intent": "Load balance traffic from web-tier to backend 10.20.0.0/24 using round-robin across 3 paths.",
     "policy_type": "load-balancing",
     "config": {"source": "web-tier", "destination": "10.20.0.0/24", "action": "allow", "protocol": "tcp", "priority": 100, "load_balancing": {"algorithm": "round-robin", "paths": ["path-1", "path-2", "path-3"]}}},
    {"intent": "Distribute traffic from app-server to 172.31.0.0/16 evenly across two links.",
     "policy_type": "load-balancing",
     "config": {"source": "app-server", "destination": "172.31.0.0/16", "action": "allow", "protocol": "any", "priority": 100, "load_balancing": {"algorithm": "round-robin", "paths": ["link-a", "link-b"]}}},
    {"intent": "Balance TCP traffic from db-client to 10.30.0.0/24 using weighted routing (70/30).",
     "policy_type": "load-balancing",
     "config": {"source": "db-client", "destination": "10.30.0.0/24", "action": "allow", "protocol": "tcp", "priority": 100, "load_balancing": {"algorithm": "weighted", "paths": [{"path": "primary", "weight": 70}, {"path": "secondary", "weight": 30}]}}},
    {"intent": "Traffic from host-a to 192.168.50.0/24 should be load balanced via ECMP.",
     "policy_type": "load-balancing",
     "config": {"source": "host-a", "destination": "192.168.50.0/24", "action": "allow", "protocol": "any", "priority": 100, "load_balancing": {"algorithm": "ecmp", "paths": ["ecmp-1", "ecmp-2", "ecmp-3", "ecmp-4"]}}},
    {"intent": "Traffic from munich can communicate with 10.100.0.0/24.",
     "policy_type": "reachability",
     "config": {"source": "munich", "destination": "10.100.0.0/24", "action": "allow", "protocol": "any", "priority": 100}},
    {"intent": "Hosts at vienna are not allowed to reach 198.51.100.0/24.",
     "policy_type": "reachability",
     "config": {"source": "vienna", "destination": "198.51.100.0/24", "action": "deny", "protocol": "any", "priority": 90}},
    {"intent": "Traffic from brussels to 10.60.0.0/24 must go through nat-box.",
     "policy_type": "waypoint",
     "config": {"source": "brussels", "destination": "10.60.0.0/24", "action": "allow", "protocol": "any", "priority": 100, "waypoints": ["nat-box"]}},
    {"intent": "All UDP traffic from sensor-cluster can reach 10.200.0.0/24.",
     "policy_type": "reachability",
     "config": {"source": "sensor-cluster", "destination": "10.200.0.0/24", "action": "allow", "protocol": "udp", "priority": 100}},
    {"intent": "TCP connections from client-zone to 172.25.0.0/24 must be load balanced across 4 paths.",
     "policy_type": "load-balancing",
     "config": {"source": "client-zone", "destination": "172.25.0.0/24", "action": "allow", "protocol": "tcp", "priority": 100, "load_balancing": {"algorithm": "round-robin", "paths": ["p1", "p2", "p3", "p4"]}}},
    {"intent": "Traffic from stockholm to 10.0.90.0/24 must be allowed.",
     "policy_type": "reachability",
     "config": {"source": "stockholm", "destination": "10.0.90.0/24", "action": "allow", "protocol": "any", "priority": 100}},
]


class RAGEngine:
    """
    Retrieval-Augmented Generation engine over NetConfEval dataset.
    Loads samples from a JSON file (or uses builtin samples as fallback),
    builds a simple TF-IDF index, and retrieves the top-k most similar
    examples for a given query intent.
    """

    def __init__(self, data_path: Optional[str] = None):
        self.samples: List[dict] = []
        self._tfidf: Optional[List[dict]] = None  # lazy-built index
        self._load(data_path)

    def _load(self, path: Optional[str]):
        if path and os.path.exists(path):
            try:
                with open(path) as f:
                    raw = json.load(f)
                # Support NetConfEval native format or flat list
                if isinstance(raw, dict) and "samples" in raw:
                    self.samples = raw["samples"]
                elif isinstance(raw, list):
                    self.samples = raw
                logger.info(f"RAG: loaded {len(self.samples)} samples from {path}")
                return
            except Exception as e:
                logger.warning(f"RAG: could not load {path}: {e} — using builtin dataset")
        self.samples = BUILTIN_SAMPLES
        logger.info(f"RAG: using builtin dataset ({len(self.samples)} samples)")

    # ── TF-IDF helpers ────────────────────────────────────────────────────────
    def _tokenize(self, text: str) -> List[str]:
        return text.lower().replace(".", " ").replace("/", " ").replace(",", " ").split()

    def _build_index(self):
        corpus = [self._tokenize(s["intent"]) for s in self.samples]
        n = len(corpus)
        df: Dict[str, int] = {}
        for doc in corpus:
            for tok in set(doc):
                df[tok] = df.get(tok, 0) + 1
        idf = {tok: math.log(n / (v + 1)) + 1 for tok, v in df.items()}
        vecs = []
        for doc in corpus:
            tf: Dict[str, float] = {}
            for tok in doc:
                tf[tok] = tf.get(tok, 0) + 1
            total = len(doc) or 1
            vec = {tok: (cnt / total) * idf.get(tok, 1) for tok, cnt in tf.items()}
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1
            vecs.append({tok: v / norm for tok, v in vec.items()})
        self._tfidf = vecs
        self._idf = idf

    def _cosine(self, query_tokens: List[str], doc_vec: dict) -> float:
        if not hasattr(self, "_idf"):
            return 0.0
        tf: Dict[str, float] = {}
        for tok in query_tokens:
            tf[tok] = tf.get(tok, 0) + 1
        total = len(query_tokens) or 1
        qvec = {tok: (cnt / total) * self._idf.get(tok, 1) for tok, cnt in tf.items()}
        norm = math.sqrt(sum(v * v for v in qvec.values())) or 1
        qvec = {tok: v / norm for tok, v in qvec.items()}
        return sum(qvec.get(tok, 0) * doc_vec.get(tok, 0) for tok in qvec)

    # ── Public API ────────────────────────────────────────────────────────────
    def retrieve(self, intent: str, policy_type: str = "all", k: int = 3) -> dict:
        """Return the k most similar ground-truth examples for the given intent."""
        if self._tfidf is None:
            self._build_index()

        query_tokens = self._tokenize(intent)
        candidates   = [
            (i, s) for i, s in enumerate(self.samples)
            if policy_type in ("all", s.get("policy_type", ""))
        ]
        scored = [
            (self._cosine(query_tokens, self._tfidf[i]), s)
            for i, s in candidates
        ]
        scored.sort(key=lambda x: -x[0])
        top = scored[:k]

        return {
            "ground_truth_count": len(top),
            "examples": [{"intent": s["intent"], "config": s.get("config", {}), "score": round(sc, 4)}
                         for sc, s in top],
        }

    def get_samples(self, policy_type: str = "all", limit: int = 20, offset: int = 0) -> dict:
        """Paginated sample retrieval for the frontend sampler UI."""
        filtered = [
            s for s in self.samples
            if policy_type in ("all", s.get("policy_type", ""))
        ]
        page = filtered[offset: offset + limit]
        return {"total": len(filtered), "samples": page}
