import asyncio
from backend.app.services.nac_service import nac_client, TEST_SIMSWAP_OCCURRED, TEST_SIMSWAP_NOT_OCCURRED

async def main():
    for number in [TEST_SIMSWAP_OCCURRED, TEST_SIMSWAP_NOT_OCCURRED]:
        result = await nac_client.check_sim_swap(number)
        print(number, "->", result)

asyncio.run(main())