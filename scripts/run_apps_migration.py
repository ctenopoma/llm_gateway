"""Run Apps migration directly (no SSL)."""
import asyncio
import asyncpg

SQL = """
CREATE TABLE IF NOT EXISTS Apps (
    app_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    owner_id VARCHAR(36) NOT NULL REFERENCES Users(oid),
    is_active BOOLEAN DEFAULT TRUE,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE UsageLogs ADD COLUMN IF NOT EXISTS app_id VARCHAR(50);

CREATE INDEX IF NOT EXISTS idx_usagelogs_app_id ON UsageLogs(app_id);
"""

async def main():
    conn = await asyncpg.connect(
        user="gateway",
        password="gateway",
        database="llm_gateway",
        host="localhost",
        port=5432,
        ssl=False,
    )

    print("Connected to database!")
    for stmt in [s.strip() for s in SQL.split(";") if s.strip()]:
        print(f"  Running: {stmt[:60]}...")
        await conn.execute(stmt)
        print("  OK")
    await conn.close()
    print("Migration complete!")

if __name__ == "__main__":
    asyncio.run(main())
