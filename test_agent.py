import asyncio
from backend.app.agents.telco_agent import run_agent

EVENT = (
    "A user is attempting a bank transfer of 15,000 SAR from phone number "
    "+99999991000. Assess the fraud risk before allowing the transaction."
)

async def main():
    result = await run_agent(EVENT)
    print("\n=== AGENT DECISION ===")
    print(result)

asyncio.run(main())