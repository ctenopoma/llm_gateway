-- Antigravity LLM Gateway v2.3 — Full Schema DDL
-- Run against a PostgreSQL 14+ instance

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================
-- 1. Users
-- =============================================
CREATE TABLE Users (
    oid VARCHAR(36) PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    display_name VARCHAR(255),

    payment_status VARCHAR(20) NOT NULL DEFAULT 'active',
    payment_valid_until DATE NOT NULL,

    webhook_url VARCHAR(512),
    total_cost_cache DECIMAL(10, 2) DEFAULT 0.00,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    last_sync_at TIMESTAMP,

    CONSTRAINT valid_payment_status CHECK (
        payment_status IN ('active', 'expired', 'banned', 'trial')
    )
);

CREATE INDEX idx_users_payment_status ON Users(payment_status);
CREATE INDEX idx_users_payment_valid_until ON Users(payment_valid_until);
CREATE INDEX idx_users_email ON Users(email);

-- =============================================
-- 2. ApiKeys (SHA-256 + Salt)
-- =============================================
CREATE TABLE ApiKeys (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_oid VARCHAR(36) NOT NULL REFERENCES Users(oid) ON DELETE CASCADE,

    -- Key Storage (SHA-256 + Salt for performance)
    hashed_key VARCHAR(64) NOT NULL UNIQUE,
    salt VARCHAR(32) NOT NULL,
    display_prefix VARCHAR(20) NOT NULL,

    -- Permissions & Limits
    allowed_models JSONB DEFAULT NULL,
    scopes JSONB DEFAULT '["chat.completions"]'::jsonb,
    allowed_ips JSONB DEFAULT NULL,

    -- Rate Limiting
    rate_limit_rpm INTEGER NOT NULL DEFAULT 60,

    -- Budget Management
    budget_monthly DECIMAL(10, 2) DEFAULT NULL,
    usage_current_month DECIMAL(10, 2) DEFAULT 0.00,
    last_reset_month VARCHAR(7),

    -- Key Lifecycle
    label VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE,
    created_by VARCHAR(36),
    expires_at TIMESTAMP,
    replaced_by UUID REFERENCES ApiKeys(id),

    created_at TIMESTAMP DEFAULT NOW(),
    last_used_at TIMESTAMP,

    CONSTRAINT positive_rate_limit CHECK (rate_limit_rpm > 0),
    CONSTRAINT positive_budget CHECK (budget_monthly IS NULL OR budget_monthly >= 0)
);

CREATE INDEX idx_apikeys_user_oid ON ApiKeys(user_oid);
CREATE INDEX idx_apikeys_hashed_key ON ApiKeys(hashed_key);
CREATE INDEX idx_apikeys_is_active ON ApiKeys(is_active);
CREATE INDEX idx_apikeys_last_reset_month ON ApiKeys(last_reset_month);
CREATE INDEX idx_apikeys_expires_at ON ApiKeys(expires_at) WHERE expires_at IS NOT NULL;

-- =============================================
-- 3. Models
-- =============================================
CREATE TABLE Models (
    id VARCHAR(50) PRIMARY KEY,
    litellm_name VARCHAR(100) NOT NULL,
    provider VARCHAR(50) NOT NULL,

    -- Pricing (JPY per 1M tokens)
    input_cost DECIMAL(10, 4) NOT NULL,
    output_cost DECIMAL(10, 4) NOT NULL,
    internal_cost DECIMAL(10, 4) DEFAULT 0,

    -- Resilience
    max_retries INTEGER DEFAULT 2,
    fallback_models JSONB DEFAULT '[]'::jsonb,

    -- Status & Traffic Control
    is_active BOOLEAN DEFAULT TRUE,
    traffic_weight FLOAT DEFAULT 1.0,

    -- Model Capabilities
    model_family VARCHAR(50),
    context_window INTEGER NOT NULL DEFAULT 4096,
    max_output_tokens INTEGER DEFAULT 2048,
    supports_streaming BOOLEAN DEFAULT TRUE,
    supports_functions BOOLEAN DEFAULT FALSE,
    supports_vision BOOLEAN DEFAULT FALSE,

    -- Metadata
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    CONSTRAINT positive_costs CHECK (
        input_cost >= 0 AND output_cost >= 0 AND internal_cost >= 0
    ),
    CONSTRAINT valid_traffic_weight CHECK (
        traffic_weight >= 0 AND traffic_weight <= 1.0
    ),
    CONSTRAINT positive_context_window CHECK (
        context_window > 0
    )
);

CREATE INDEX idx_models_is_active ON Models(is_active);
CREATE INDEX idx_models_provider ON Models(provider);
CREATE INDEX idx_models_model_family ON Models(model_family);

-- =============================================
-- 4. ModelEndpoints
-- =============================================
CREATE TABLE ModelEndpoints (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_id VARCHAR(50) NOT NULL REFERENCES Models(id) ON DELETE CASCADE,

    -- Endpoint Configuration
    endpoint_type VARCHAR(20) NOT NULL,
    base_url VARCHAR(512) NOT NULL,
    api_key_ref VARCHAR(100),

    -- Load Balancing & Routing
    routing_priority INTEGER DEFAULT 100,
    routing_strategy VARCHAR(30) DEFAULT 'round-robin',

    -- Health Check Configuration
    health_check_url VARCHAR(512),
    health_check_interval INTEGER DEFAULT 60,
    health_check_timeout INTEGER DEFAULT 10,
    next_check_at TIMESTAMP DEFAULT NOW(),

    -- Performance Settings
    timeout_seconds INTEGER DEFAULT 120,
    max_concurrent_requests INTEGER DEFAULT 10,

    -- Model-specific Configuration
    model_config JSONB,

    -- Status Tracking
    is_active BOOLEAN DEFAULT TRUE,
    last_health_check TIMESTAMP,
    health_status VARCHAR(20) DEFAULT 'unknown',
    consecutive_failures INTEGER DEFAULT 0,

    -- Performance Metrics
    avg_latency_ms INTEGER DEFAULT 0,
    total_requests BIGINT DEFAULT 0,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    CONSTRAINT valid_endpoint_type CHECK (
        endpoint_type IN ('vllm', 'ollama', 'tgi', 'custom')
    ),
    CONSTRAINT valid_health_status CHECK (
        health_status IN ('healthy', 'degraded', 'down', 'unknown')
    ),
    CONSTRAINT valid_routing_strategy CHECK (
        routing_strategy IN ('round-robin', 'usage-based', 'latency-based', 'random')
    ),
    CONSTRAINT positive_timeouts CHECK (
        timeout_seconds > 0 AND health_check_timeout > 0
    ),
    CONSTRAINT positive_concurrency CHECK (
        max_concurrent_requests > 0
    )
);

CREATE INDEX idx_model_endpoints_model_id ON ModelEndpoints(model_id);
CREATE INDEX idx_model_endpoints_health_status ON ModelEndpoints(health_status);
CREATE INDEX idx_model_endpoints_is_active ON ModelEndpoints(is_active);
CREATE INDEX idx_endpoints_next_check ON ModelEndpoints(is_active, next_check_at);

-- =============================================
-- 5. UsageLogs (Partitioned by Month)
-- =============================================
CREATE TABLE UsageLogs (
    id BIGSERIAL,

    -- User Identification
    user_oid VARCHAR(36) NOT NULL REFERENCES Users(oid),
    api_key_id UUID REFERENCES ApiKeys(id),
    app_id VARCHAR(50), -- Added in v2.3

    -- Request Identification
    request_id VARCHAR(100),
    ip_address INET,
    user_agent TEXT,

    -- Model Information
    requested_model VARCHAR(50) NOT NULL,
    actual_model VARCHAR(50) NOT NULL,
    endpoint_id UUID REFERENCES ModelEndpoints(id),

    -- Token Usage
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,

    -- Cost Calculation
    cost DECIMAL(10, 4) NOT NULL DEFAULT 0,
    internal_cost DECIMAL(10, 4) DEFAULT 0,

    -- Request Status
    status VARCHAR(20) NOT NULL DEFAULT 'completed',
    error_message TEXT,
    error_code VARCHAR(50),

    -- Timing Metrics
    created_at TIMESTAMP DEFAULT NOW() NOT NULL,
    completed_at TIMESTAMP,
    latency_ms INTEGER,
    ttft_ms INTEGER,

    -- Metadata (NO prompt content)
    request_metadata JSONB,

    PRIMARY KEY (id, created_at),

    CONSTRAINT valid_status CHECK (
        status IN ('pending', 'completed', 'failed', 'cancelled')
    )
) PARTITION BY RANGE (created_at);

-- Create initial partitions (2026)
CREATE TABLE UsageLogs_2026_01 PARTITION OF UsageLogs
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');

CREATE TABLE UsageLogs_2026_02 PARTITION OF UsageLogs
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');

CREATE TABLE UsageLogs_2026_03 PARTITION OF UsageLogs
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');

CREATE TABLE UsageLogs_2026_04 PARTITION OF UsageLogs
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');

CREATE TABLE UsageLogs_2026_05 PARTITION OF UsageLogs
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

CREATE TABLE UsageLogs_2026_06 PARTITION OF UsageLogs
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE UsageLogs_2026_07 PARTITION OF UsageLogs
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

CREATE TABLE UsageLogs_2026_08 PARTITION OF UsageLogs
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

CREATE TABLE UsageLogs_2026_09 PARTITION OF UsageLogs
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');

CREATE TABLE UsageLogs_2026_10 PARTITION OF UsageLogs
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');

CREATE TABLE UsageLogs_2026_11 PARTITION OF UsageLogs
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');

CREATE TABLE UsageLogs_2026_12 PARTITION OF UsageLogs
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');

-- Indexes on partitioned table
CREATE INDEX idx_usagelogs_user_oid ON UsageLogs(user_oid);
CREATE INDEX idx_usagelogs_api_key_id ON UsageLogs(api_key_id);
CREATE INDEX idx_usagelogs_created_at ON UsageLogs(created_at DESC);
CREATE INDEX idx_usagelogs_status ON UsageLogs(status);
CREATE INDEX idx_usagelogs_request_id ON UsageLogs(request_id);
CREATE INDEX idx_usagelogs_actual_model ON UsageLogs(actual_model);
CREATE INDEX idx_usagelogs_endpoint_id ON UsageLogs(endpoint_id);

-- =============================================
-- 5.5. Apps (Added in v2.3)
-- =============================================
CREATE TABLE Apps (
    app_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    owner_id VARCHAR(36) NOT NULL REFERENCES Users(oid),
    is_active BOOLEAN DEFAULT TRUE,
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_apps_owner_id ON Apps(owner_id);

-- =============================================
-- 6. AuditLogs
-- =============================================
CREATE TABLE AuditLogs (
    id BIGSERIAL PRIMARY KEY,
    admin_oid VARCHAR(36) NOT NULL REFERENCES Users(oid),
    action VARCHAR(50) NOT NULL,
    target_type VARCHAR(20),
    target_id VARCHAR(100),
    metadata JSONB,
    timestamp TIMESTAMP DEFAULT NOW(),
    ip_address INET,
    user_agent TEXT
);

CREATE INDEX idx_auditlogs_admin_oid ON AuditLogs(admin_oid);
CREATE INDEX idx_auditlogs_action ON AuditLogs(action);
CREATE INDEX idx_auditlogs_timestamp ON AuditLogs(timestamp DESC);
CREATE INDEX idx_auditlogs_target ON AuditLogs(target_type, target_id);

-- =============================================
-- System Admin User (used for audit logging from admin panel)
-- This user must NOT be deleted or modified.
-- =============================================
INSERT INTO Users (oid, email, display_name, payment_status, payment_valid_until)
VALUES ('SYSTEM_ADMIN', 'system@internal', 'System Administrator', 'active', '2099-12-31')
ON CONFLICT (oid) DO NOTHING;
