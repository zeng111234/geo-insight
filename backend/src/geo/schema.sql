CREATE TABLE IF NOT EXISTS tenant (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    plan TEXT NOT NULL DEFAULT 'internal',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_user (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenant(id),
    email TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'operator', 'viewer')),
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    UNIQUE (tenant_id, email)
);

CREATE TABLE IF NOT EXISTS brand (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenant(id),
    name TEXT NOT NULL,
    industry TEXT,
    region TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brand_alias (
    id TEXT PRIMARY KEY,
    brand_id TEXT NOT NULL REFERENCES brand(id),
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL CHECK (alias_type IN ('full', 'short', 'en', 'misspell')),
    is_active INTEGER NOT NULL DEFAULT 1,
    UNIQUE (brand_id, alias)
);

CREATE TABLE IF NOT EXISTS competitor_set (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenant(id),
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS competitor (
    id TEXT PRIMARY KEY,
    competitor_set_id TEXT NOT NULL REFERENCES competitor_set(id),
    name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS tracker (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenant(id),
    brand_id TEXT NOT NULL REFERENCES brand(id),
    competitor_set_id TEXT REFERENCES competitor_set(id),
    name TEXT NOT NULL,
    industry TEXT,
    region TEXT,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'active', 'paused', 'archived')),
    owner_id TEXT REFERENCES app_user(id),
    settings TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tracker_tenant_status ON tracker(tenant_id, status);

CREATE TABLE IF NOT EXISTS question_set (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenant(id),
    tracker_id TEXT NOT NULL REFERENCES tracker(id),
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS question (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    question_set_id TEXT NOT NULL REFERENCES question_set(id),
    text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'long_tail' CHECK (tier IN ('core', 'long_tail')),
    intent TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at TEXT NOT NULL,
    UNIQUE (question_set_id, normalized_text)
);
CREATE INDEX IF NOT EXISTS idx_question_set_tier ON question(question_set_id, tier, status);

CREATE TABLE IF NOT EXISTS sampling_plan (
    id TEXT PRIMARY KEY,
    tracker_id TEXT NOT NULL REFERENCES tracker(id),
    channel TEXT NOT NULL CHECK (channel IN ('api', 'web')),
    model_keys TEXT NOT NULL,
    core_times INTEGER NOT NULL DEFAULT 3,
    long_tail_times INTEGER NOT NULL DEFAULT 1,
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    effective_from TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    tracker_id TEXT NOT NULL REFERENCES tracker(id),
    trigger TEXT NOT NULL CHECK (trigger IN ('schedule', 'manual', 'backfill')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'completed', 'partial', 'failed')),
    scheduled_for TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    stats TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS run_item (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES run(id),
    question_id TEXT NOT NULL REFERENCES question(id),
    model_key TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('api', 'web')),
    sample_idx INTEGER NOT NULL CHECK (sample_idx BETWEEN 1 AND 3),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'skipped')),
    error_code TEXT,
    attempted_at TEXT,
    UNIQUE (run_id, question_id, model_key, channel, sample_idx)
);

CREATE TABLE IF NOT EXISTS answer (
    id TEXT PRIMARY KEY,
    run_item_id TEXT UNIQUE REFERENCES run_item(id),
    tenant_id TEXT NOT NULL,
    tracker_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    model_key TEXT NOT NULL,
    model_version TEXT,
    channel TEXT NOT NULL CHECK (channel IN ('api', 'web')),
    raw_uri TEXT NOT NULL,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    latency_ms INTEGER,
    token_in INTEGER,
    token_out INTEGER,
    cost REAL,
    is_valid INTEGER NOT NULL DEFAULT 1,
    invalid_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_answer_tracker_time ON answer(tenant_id, tracker_id, collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_answer_question_model ON answer(question_id, model_key, collected_at DESC);

CREATE TABLE IF NOT EXISTS answer_block (
    id TEXT PRIMARY KEY,
    answer_id TEXT NOT NULL REFERENCES answer(id),
    seq INTEGER NOT NULL,
    block_type TEXT NOT NULL CHECK (block_type IN ('paragraph', 'list_item', 'table_row')),
    text TEXT NOT NULL,
    list_index INTEGER
);

CREATE TABLE IF NOT EXISTS mention (
    id TEXT PRIMARY KEY,
    answer_id TEXT NOT NULL REFERENCES answer(id),
    brand_id TEXT NOT NULL REFERENCES brand(id),
    subject_type TEXT NOT NULL DEFAULT 'own' CHECK (subject_type IN ('own', 'competitor')),
    matched_alias TEXT NOT NULL,
    match_type TEXT NOT NULL CHECK (match_type IN ('exact', 'normalized', 'fuzzy')),
    position INTEGER,
    position_type TEXT,
    sentiment TEXT NOT NULL CHECK (sentiment IN ('pos', 'neu', 'neg')),
    sentiment_confidence REAL,
    snippet TEXT NOT NULL,
    UNIQUE (answer_id, brand_id),
    CHECK (position IS NULL OR position >= 1)
);
CREATE INDEX IF NOT EXISTS idx_mention_brand_sentiment ON mention(brand_id, sentiment);

CREATE TABLE IF NOT EXISTS citation (
    id TEXT PRIMARY KEY,
    answer_id TEXT NOT NULL REFERENCES answer(id),
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL,
    domain TEXT NOT NULL,
    media_name TEXT,
    media_type TEXT NOT NULL,
    owner_type TEXT NOT NULL DEFAULT 'media' CHECK (owner_type IN ('brand', 'competitor', 'media')),
    position INTEGER NOT NULL,
    cite_type TEXT NOT NULL CHECK (cite_type IN ('inline', 'reference', 'video')),
    published_at TEXT,
    published_at_unknown INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_citation_answer ON citation(answer_id);
CREATE INDEX IF NOT EXISTS idx_citation_domain_time ON citation(domain, published_at);

CREATE TABLE IF NOT EXISTS report (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    tracker_id TEXT NOT NULL REFERENCES tracker(id),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'generating', 'ready', 'failed')),
    metric_version TEXT NOT NULL,
    model_set_hash TEXT NOT NULL,
    competitor_set_version INTEGER,
    created_by TEXT,
    created_at TEXT NOT NULL,
    ready_at TEXT,
    CHECK (period_start < period_end)
);

CREATE TABLE IF NOT EXISTS report_snapshot (
    report_id TEXT PRIMARY KEY REFERENCES report(id),
    payload TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    actor_id TEXT,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT,
    before_json TEXT,
    after_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_time ON audit_log(tenant_id, created_at DESC);

-- ============================================================
-- 002 内容工作室：文章、事实、渠道账号、发布记录
-- ============================================================

CREATE TABLE IF NOT EXISTS article (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    tracker_id TEXT NOT NULL REFERENCES tracker(id),
    question_id TEXT REFERENCES question(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    template_key TEXT,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'review', 'published')),
    version INTEGER NOT NULL DEFAULT 1,
    brief TEXT,
    goal TEXT,
    acceptance TEXT,
    brand_material TEXT,
    fact_threshold TEXT,
    compliance_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_article_tracker ON article(tracker_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS article_fact (
    id TEXT PRIMARY KEY,
    article_id TEXT NOT NULL REFERENCES article(id),
    claim TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('confirmed', 'pending', 'rejected')),
    source_ref TEXT,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_fact_article ON article_fact(article_id, position);

CREATE TABLE IF NOT EXISTS channel_account (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    platform_key TEXT NOT NULL,
    platform_name TEXT NOT NULL,
    account_name TEXT,
    status TEXT NOT NULL DEFAULT 'disconnected'
        CHECK (status IN ('connected', 'disconnected', 'expired')),
    login_mode TEXT,
    last_checked_at TEXT,
    UNIQUE (tenant_id, platform_key)
);

CREATE TABLE IF NOT EXISTS publication (
    id TEXT PRIMARY KEY,
    article_id TEXT NOT NULL REFERENCES article(id),
    channel_id TEXT NOT NULL REFERENCES channel_account(id),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'published', 'failed')),
    published_url TEXT,
    published_at TEXT,
    failure_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (article_id, channel_id)
);
CREATE INDEX IF NOT EXISTS idx_pub_article ON publication(article_id);
