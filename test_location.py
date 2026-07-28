import asyncio
from backend.app.services.nac_service import nac_client, TEST_LOCATION_IN_AREA, TEST_LOCATION_NOT_IN_AREA

async def main():
    for number in [TEST_LOCATION_IN_AREA, TEST_LOCATION_NOT_IN_AREA]:
        result = await nac_client.verify_location(
            phone_number=number,
            latitude=25.276987,   # arbitrary — simulator response is keyed by phone number, not real coords
            longitude=55.296249,
            radius_meters=1000,
        )
        print(number, "->", result)

asyncio.run(main())