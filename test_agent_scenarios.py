import asyncio
from backend.app.agents.telco_agent import run_agent

SCENARIOS = {
    "clean_transaction": (
        "A user is attempting a bank transfer of 15,000 SAR from phone number "
        "+99999991001. Assess the fraud risk before allowing the transaction."
    ),
    "pilgrimage_geofence": (
        "A pilgrimage group coordinator wants to confirm that pilgrim device "
        "+99999991001 is currently within the designated safe zone centered at "
        "21.4225, 39.8262 (Masjid al-Haram), within a 2km radius, before allowing "
        "them to proceed to the next checkpoint."
    ),
    "emergency_video": (
        "An emergency responder at scene needs guaranteed low-latency video "
        "streaming from device +9999123456 to the command center application "
        "server at 233.252.0.2 immediately — this is a life-safety situation."
    ),
}

async def main():
    for name, event in SCENARIOS.items():
        print(f"\n{'='*20} {name} {'='*20}")
        result = await run_agent(event)
        print("DECISION:", result)

asyncio.run(main())