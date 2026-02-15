-- Create Apps table
CREATE TABLE IF NOT EXISTS Apps (
    app_id VARCHAR(50) PRIMARY KEY, -- e.g. 'chat-app-v1'
    name VARCHAR(255) NOT NULL,
    owner_id VARCHAR(36) NOT NULL REFERENCES Users(oid),
    is_active BOOLEAN DEFAULT TRUE,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Add app_id to UsageLogs (and partitions)
ALTER TABLE UsageLogs ADD COLUMN IF NOT EXISTS app_id VARCHAR(50);
-- Note: In Postgres, adding column to partitioned parent adds to children automatically.

-- Index for analytics
CREATE INDEX IF NOT EXISTS idx_usagelogs_app_id ON UsageLogs(app_id);
-- Note: Indexes on partitioned tables are supported but sometimes need per-partition. 
-- In PG11+, creating on parent propagates. We'll assume typical behavior.
