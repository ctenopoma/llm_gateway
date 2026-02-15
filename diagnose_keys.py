
import asyncio
from app import database as db
from app.config import get_settings

async def main():
    await db.init_db()
    
    print("--- Current API Keys in DB ---")
    rows = await db.fetch_all("SELECT id, is_active, label, left(hashed_key, 10) as hash_prefix FROM ApiKeys")
    
    found = False
    target_id = "eb1e449f-b91b-4f61-a095-d95d54bd83a1"
    
    for r in rows:
        sid = str(r["id"])
        print(f"ID: {sid} | Active: {r['is_active']} | Label: {r['label']}")
        if sid == target_id:
            found = True
            
    if found:
        print(f"\nTARGET KEY {target_id} FOUND in DB.")
    else:
        print(f"\nTARGET KEY {target_id} NOT FOUND in DB.")

    await db.close_db()

if __name__ == "__main__":
    asyncio.run(main())
