import asyncio
from backend.app.services.nac_service import nac_client, TEST_DEVICE_GENERIC

async def main():
    result = await nac_client.request_qod_session(
        phone_number=TEST_DEVICE_GENERIC,
        service_ip="233.252.0.2",  # the example IP from Nokia's own QoD docs
        qos_profile="QOS_L",
        duration_seconds=3600,
    )
    print(result)

asyncio.run(main())