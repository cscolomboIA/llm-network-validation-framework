# Arquitetura do NetValidAI

## Visão Conceitual

```
┌─────────────────────────────────────────────────────────────────────┐
│                        NetValidAI Platform                          │
│                                                                     │
│  ┌───────────┐    ┌───────────┐    ┌──────────┐    ┌────────────┐  │
│  │  Frontend │───▶│  API GW   │───▶│ Verifier │───▶│  Mininet   │  │
│  │ (HTML/JS) │    │ (FastAPI) │    │ (3 etapas│    │  Runner    │  │
│  └───────────┘    └─────┬─────┘    └──────────┘    └────────────┘  │
│                         │                                           │
│                   ┌─────▼─────┐   ┌──────────────┐                 │
│                   │    LLM    │   │  RAG Engine  │                 │
│                   │ Providers │   │ (NetConfEval)│                 │
│                   └───────────┘   └──────────────┘                 │
│                                                                     │
│                   ┌─────────────────────────┐                      │
│                   │   Self-Healing Agent    │                      │
│                   │  (até 3 tentativas)     │                      │
│                   └─────────────────────────┘                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Componentes

### API Gateway (`api-gateway/main.py`)

Ponto central de orquestração. Recebe a intenção do usuário via HTTP POST `/api/pipeline/run` e coordena a execução sequencial das 4 etapas. Expõe também endpoints auxiliares para:
- `/health` — status do servidor
- `/api/models` — lista de modelos disponíveis
- `/api/validate/intent` — validação rápida da intenção
- `/api/dataset/samples` — amostras paginadas do NetConfEval

### Verifier (`verifier/`)

Módulo responsável pelas Etapas 1–3:

| Arquivo | Etapa | Descrição |
|---|---|---|
| `syntactic.py` | 1 | Chama o LLM, extrai e valida o JSON |
| `conformance.py` | 2 | Valida schema YANG + algoritmo DETOX |
| `semantic.py` | 3 | Calcula similaridade com ground truth via RAG |

### RAG Engine (`rag-engine/engine.py`)

Implementa recuperação TF-IDF + cosine similarity sobre o dataset NetConfEval. Fornece os `k` exemplos mais similares como contexto para o LLM e como referência para cálculo de similaridade na Etapa 3.

### Self-Healing Agent (`self-healing-agent/agent.py`)

Agente de autocorreção que reformula o prompt incorporando feedback específico de erro da etapa que falhou. Executa até `MAX_HEALING_ATTEMPTS` (padrão: 3) tentativas antes de retornar falha definitiva.

### Mininet Runner (`mininet-runner/orchestrator.py`)

Traduz o JSON validado em comandos Mininet, instancia a topologia de rede emulada e executa testes de conectividade via ICMP (ping -c 3). Suporta modo `real` (Mininet nativo) e `simulated` (para demonstração sem Linux).

### Frontend (`frontend/index.html`)

Aplicação web de página única com:
- **Aba Pipeline**: execução passo-a-passo com visualização dos 4 painéis de resultado
- **Aba Visual Builder**: diagrama interativo dos componentes do pipeline (drag-and-drop)
- Sampler integrado com os 1.665 samples do NetConfEval
- Controle de batch size com alerta visual ao cruzar o limiar crítico de 20

## Fluxo de Dados

```
POST /api/pipeline/run
  {intent, model, policy_type, batch_size}
        │
        ▼
  RAG.retrieve(intent, k=3)    ← busca exemplos similares no NetConfEval
        │
        ▼
  Stage 1: LLM call + JSON extraction
        │ fail ──→ return {all_pass: false}
        ▼ pass
  Stage 2: YANG schema + DETOX checks
        │ fail ──→ Self-Healing Agent → retry Stage 1 (≤3x)
        ▼ pass
  Stage 3: TF-IDF similarity vs ground truth
        │ merged into Stage 2 result
        ▼
  Stage 4: Mininet topology + ping test
        │
        ▼
  return {all_pass, stages, rag_context}
```

## Decisões de Design

### Por que temperatura = 0,0?
Segue o protocolo do benchmark NetConfEval [Dahlmann et al. 2024] para garantir reprodutibilidade e comparabilidade com a literatura.

### Por que TF-IDF e não embeddings densos?
TF-IDF oferece interpretabilidade, zero dependência de GPU e resultado determinístico — adequado para um framework de verificação. Embeddings densos são listados como trabalho futuro.

### Por que modo simulado no Mininet?
O Mininet requer kernel Linux e privilégios root, inviabilizando execução em Docker e em ambientes de CI sem customização. O modo simulado reproduz a distribuição estatística de resultados observada nos experimentos do artigo, permitindo demonstração em qualquer OS.
