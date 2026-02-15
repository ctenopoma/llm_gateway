
import asyncio
from app import database as db

async def main():
    await db.init_db()
    
    print("--- Tables in DB ---")
    rows = await db.fetch_all("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    for r in rows:
        print(r["table_name"])
    
    await db.close_db()

if __name__ == "__main__":
    asyncio.run(main())
