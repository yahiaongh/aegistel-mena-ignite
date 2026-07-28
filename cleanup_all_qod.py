# cleanup_all_qod.py
import asyncio
from backend.app.services.nac_service import nac_client, TEST_DEVICE_GENERIC

async def main():
    sessions = await asyncio.to_thread(
        nac_client.client.qod.retrieve_sessions_v1, device={"phone_number": TEST_DEVICE_GENERIC}
    )
    for s in sessions:
        print("deleting", s.session_id)
        try:
            await asyncio.to_thread(nac_client.client.qod.delete_session_v1, session_id=s.session_id)
        except Exception as e:
            print("  failed:", e)

asyncio.run(main())