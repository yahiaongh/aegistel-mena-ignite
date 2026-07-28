import os
import json
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

from ..services.nac_service import nac_client

env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_sim_swap",
            "description": "Check whether a phone number's SIM was recently swapped. Use for fraud risk on logins, transactions, or identity-sensitive actions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {"type": "string", "description": "E.164 number, e.g. +9999123456"},
                    "max_age_hours": {"type": "integer", "description": "Default 24"},
                },
                "required": ["phone_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_location",
            "description": "Verify whether a device is within a geographic circle. Use for geofencing, logistics, or safety-zone claims.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {"type": "string"},
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "radius_meters": {"type": "integer", "description": "Default 1000"},
                },
                "required": ["phone_number", "latitude", "longitude"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_qod_session",
            "description": "Request a network Quality-on-Demand boost for a device. Use for emergencies, live video, or situations needing guaranteed bandwidth/latency.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {"type": "string"},
                    "service_ip": {"type": "string", "description": "IPv4 of the application server"},
                    "qos_profile": {"type": "string", "enum": ["QOS_E", "QOS_S", "QOS_M", "QOS_L"]},
                    "duration_seconds": {"type": "integer", "description": "Default 3600, max 86400"},
                },
                "required": ["phone_number", "service_ip"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_number",
            "description": "Verify seamless carrier line identity matching without SMS OTPs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {"type": "string"}
                },
                "required": ["phone_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_congestion",
            "description": "Fetch real-time network congestion and crowding levels for smart city/pilgrimage management.",
            "parameters": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number"},
                    "longitude": {"type": "number"},
                    "radius_meters": {"type": "integer", "default": 1000}
                },
                "required": ["latitude", "longitude"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_reachability",
            "description": "Check device network reachability and connectivity status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {"type": "string"}
                },
                "required": ["phone_number"],
            },
        },
    },
]

TOOL_IMPL = {
    "check_sim_swap": nac_client.check_sim_swap,
    "verify_location": nac_client.verify_location,
    "request_qod_session": nac_client.request_qod_session,
    "verify_number": nac_client.verify_number,
    "get_congestion": nac_client.get_congestion,
    "check_reachability": nac_client.check_reachability,
}

# SYSTEM_PROMPT = (
#     "You are AegisTel's autonomous telecom-aware agent. You receive real-world events "
#     "(logins, transactions, emergencies, location claims) and decide which CAMARA network "
#     "signals to check before recommending an action. Only call tools relevant to the event — "
#     "do not call all of them by default. After tool results come back, explain your reasoning "
#     "and give a final recommendation (ALLOW, BLOCK, or ESCALATE) in plain language.\n\n"
#     "When requesting a QoD session, choose the profile by actual traffic shape, not by how "
#     "urgent the situation sounds:\n"
#     "- QOS_E: stable latency under congestion, limited bandwidth — voice calls, control signals\n"
#     "- QOS_S / QOS_M: moderate throughput — standard app traffic, telemetry\n"
#     "- QOS_L: high/unlimited throughput — video streaming, large data transfer\n"
#     "Live video always needs QOS_L or QOS_M, regardless of how urgent the scenario is."
# )
SYSTEM_PROMPT = (
    "You are AegisTel's autonomous telco-aware AI guard. You orchestrate 6 GSMA Open Gateway CAMARA APIs:\n"
    "1. check_sim_swap\n2. verify_location\n3. request_qod_session\n4. verify_number\n5. get_congestion\n6. check_reachability\n\n"
    "Analyze incoming events, dynamically chain the required network signals, evaluate safety/fraud risks, "
    "and provide a structured final recommendation (ALLOW, BLOCK, or ESCALATE) with clear technical reasoning."
    "When requesting a QoD session, choose the profile by actual traffic shape, not by how "
    "urgent the situation sounds:\n"
    "- QOS_E: stable latency under congestion, limited bandwidth — voice calls, control signals\n"
    "- QOS_S / QOS_M: moderate throughput — standard app traffic, telemetry\n"
    "- QOS_L: high/unlimited throughput — video streaming, large data transfer\n"
    "Live video always needs QOS_L or QOS_M, regardless of how urgent the scenario is."
)

# async def run_agent(event_description: str, max_iterations: int = 5) -> str:
#     messages = [
#         {"role": "system", "content": SYSTEM_PROMPT},
#         {"role": "user", "content": event_description},
#     ]

#     for _ in range(max_iterations):
#         response = await asyncio.to_thread(
#             groq_client.chat.completions.create,
#             model=MODEL,
#             messages=messages,
#             tools=TOOLS,
#             tool_choice="auto",
#         )
#         choice = response.choices[0].message

#         if not choice.tool_calls:
#             # Model is done reasoning — this is the final natural-language answer.
#             return choice.content

#         messages.append({
#             "role": "assistant",
#             "content": choice.content,
#             "tool_calls": [
#                 {
#                     "id": call.id,
#                     "type": "function",
#                     "function": {
#                         "name": call.function.name,
#                         "arguments": call.function.arguments,
#                     },
#                 }
#                 for call in choice.tool_calls
#             ],
#         })

#         print(f"[agent] calling {len(choice.tool_calls)} tool(s):")
#         for call in choice.tool_calls:
#             args = json.loads(call.function.arguments)
#             print(f"  -> {call.function.name}({args})")
#             result = await TOOL_IMPL[call.function.name](**args)
#             messages.append({
#                 "role": "tool",
#                 "tool_call_id": call.id,
#                 "name": call.function.name,
#                 "content": json.dumps(result, default=str),
#             })

#     return "Agent stopped: reached max reasoning iterations without a final answer."
async def run_agent_stream(event_description: str, max_iterations: int = 5):
    """Yields intermediate execution trace steps for real-time frontend visualization."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": event_description},
    ]

    for _ in range(max_iterations):
        response = await asyncio.to_thread(
            groq_client.chat.completions.create,
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        choice = response.choices[0].message

        if not choice.tool_calls:
            yield {"type": "final_decision", "content": choice.content}
            return

        messages.append({
            "role": "assistant",
            "content": choice.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.function.name, "arguments": call.function.arguments},
                }
                for call in choice.tool_calls
            ],
        })

        for call in choice.tool_calls:
            args = json.loads(call.function.arguments)
            yield {"type": "tool_start", "tool": call.function.name, "args": args}
            
            result = await TOOL_IMPL[call.function.name](**args)
            
            yield {"type": "tool_result", "tool": call.function.name, "result": result}
            
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.function.name,
                "content": json.dumps(result, default=str),
            })

async def run_agent(event_description: str) -> str:
    final_output = ""
    async for step in run_agent_stream(event_description):
        if step["type"] == "final_decision":
            final_output = step["content"]
    return final_output