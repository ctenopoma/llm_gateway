
import asyncio
import asyncpg
from app import database as db

async def main():
    await db.init_db()
    
    print("--- Creating missing tables ---")
    
    # We need access to direct SQL from init_db.sql logic
    # But for now, let's just create them from the logic I know
    
    # AuditLogs
    await db.execute("""
    CREATE TABLE IF NOT EXISTS AuditLogs (
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
    CREATE INDEX IF NOT EXISTS idx_auditlogs_admin_oid ON AuditLogs(admin_oid);
    CREATE INDEX IF NOT EXISTS idx_auditlogs_action ON AuditLogs(action);
    CREATE INDEX IF NOT EXISTS idx_auditlogs_timestamp ON AuditLogs(timestamp DESC);
    CREATE INDEX IF NOT EXISTS idx_auditlogs_target ON AuditLogs(target_type, target_id);
    """)
    print("AuditLogs created.")

    # UsageLogs (partitioned)
    # Check if partitioned table exists first (it shouldn't)
    try:
        await db.execute("""
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
        """)
        print("UsageLogs table created.")
        
        # Create partitions
        for i in range(1, 13):
            month_str = f"2026-{i:02d}"
            next_month_str = f"2026-{i+1:02d}" if i < 12 else "2027-01"
            
            await db.execute(f"""
            CREATE TABLE IF NOT EXISTS UsageLogs_{month_str.replace('-', '_')} PARTITION OF UsageLogs
                FOR VALUES FROM ('{month_str}-01') TO ('{next_month_str}-01');
            """)
        print("UsageLogs partitions created (2026).")

        # Indexes
        await db.execute("""
        CREATE INDEX idx_usagelogs_user_oid ON UsageLogs(user_oid);
        CREATE INDEX idx_usagelogs_api_key_id ON UsageLogs(api_key_id);
        CREATE INDEX idx_usagelogs_created_at ON UsageLogs(created_at DESC);
        CREATE INDEX idx_usagelogs_status ON UsageLogs(status);
        CREATE INDEX idx_usagelogs_request_id ON UsageLogs(request_id);
        CREATE INDEX idx_usagelogs_actual_model ON UsageLogs(actual_model);
        CREATE INDEX idx_usagelogs_endpoint_id ON UsageLogs(endpoint_id);
        CREATE INDEX idx_usagelogs_app_id ON UsageLogs(app_id);
        """)
        print("UsageLogs indexes created.")
        
    except Exception as e:
        print(f"Error creating UsageLogs: {e}")

    await db.close_db()

if __name__ == "__main__":
    asyncio.run(main())
