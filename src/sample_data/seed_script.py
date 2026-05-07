import asyncio
import os

from src.services.postgres_service import postgres_service


async def main():
    reset = os.getenv("WAFER_SEED_RESET", "false").lower() == "true"
    result = await postgres_service.seed_demo_data(reset=reset)
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
