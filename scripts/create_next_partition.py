"""
Cron script: create next month's UsageLogs partition.
Run monthly: 0 0 1 * * python scripts/create_next_partition.py
"""

import asyncio
import os
from datetime import datetime, timedelta

import asyncpg


async def create_next_month_partition():
    database_url = os.environ.get(
        "DATABASE_URL", "postgresql://gateway:gateway@localhost:5432/llm_gateway"
    )
    conn = await asyncpg.connect(database_url)

    next_month = datetime.now() + timedelta(days=32)
    partition_name = f"UsageLogs_{next_month.strftime('%Y_%m')}"
    start_date = next_month.replace(day=1).strftime("%Y-%m-%d")

    if next_month.month == 12:
        end_date = f"{next_month.year + 1}-01-01"
    else:
        end_date = f"{next_month.year}-{next_month.month + 1:02d}-01"

    try:
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {partition_name} PARTITION OF UsageLogs
            FOR VALUES FROM ('{start_date}') TO ('{end_date}');
            """
        )
        print(f"Created partition: {partition_name} [{start_date} → {end_date})")
    except Exception as e:
        print(f"Error creating partition: {e}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(create_next_month_partition())
