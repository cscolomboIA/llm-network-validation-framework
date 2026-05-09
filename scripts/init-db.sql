-- NetValidAI — Schema PostgreSQL
-- Persiste runs do pipeline, métricas de benchmark e histórico de self-healing

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    intent      TEXT NOT NULL,
    model       TEXT NOT NULL,
    policy_type TEXT NOT NULL DEFAULT 'reachability',
    batch_size  INT  NOT NULL DEFAULT 1,

    -- Resultados de cada etapa
    syntax_pass     BOOLEAN,
    conformance_pass BOOLEAN,
    semantic_pass   BOOLEAN,
    mininet_pass    BOOLEAN,
    similarity_score FLOAT,
    packet_loss      FLOAT,

    -- Self-healing
    healing_triggered BOOLEAN DEFAULT FALSE,
    healing_success   BOOLEAN,
    healing_attempts  INT,

    -- Artefatos
    generated_config  JSONB,
    corrected_config  JSONB,
    verify_result     JSONB,
    ping_output       TEXT,
    error_classification TEXT
);

CREATE TABLE IF NOT EXISTS benchmark_runs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    model       TEXT NOT NULL,
    policy_type TEXT NOT NULL,
    batch_size  INT  NOT NULL,
    syntactic_accuracy  FLOAT,
    operational_accuracy FLOAT,
    avg_similarity      FLOAT,
    total_samples       INT
);

CREATE INDEX IF NOT EXISTS idx_runs_model ON pipeline_runs(model);
CREATE INDEX IF NOT EXISTS idx_runs_policy ON pipeline_runs(policy_type);
CREATE INDEX IF NOT EXISTS idx_runs_created ON pipeline_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bench_model ON benchmark_runs(model, policy_type);
