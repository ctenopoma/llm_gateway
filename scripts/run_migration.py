import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import database as db
from app.config import get_settings

async def run_migration():
    print("Connecting to database...")
    await db.init_db()
    
    migration_file = os.path.join(os.path.dirname(__file__), "migrate_v2_apps.sql")
    print(f"Reading migration file: {migration_file}")
    
    with open(migration_file, "r", encoding="utf-8") as f:
        sql = f.read()
        
    print("Executing migration...")
    try:
        await db.execute(sql)
        print("Migration successful!")
    except Exception as e:
        print(f"Migration failed: {e}")
    finally:
        await db.close_db()

if __name__ == "__main__":
    asyncio.run(run_migration())
