import os
import json
from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, END
from crewai import Agent, Task, Crew, Process
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from qdrant_client import QdrantClient
from mem0 import Memory

# ==============================================================================
# 1. HYBRID LLM INITIALIZATION
# ==============================================================================
llm_reasoning = ChatGoogleGenerativeAI(
    model="gemini-2.5-pro",
    google_api_key=os.getenv("GEMINI_API_KEY"),
    temperature=0.2
)

llm_fast_action = ChatGroq(
    model="llama-3.3-70b-versatile",
    groq_api_key=os.getenv("GROQ_API_KEY"),
    temperature=0.0
)

# ==============================================================================
# 2. MEMORY SYSTEM (Mem0 + Qdrant Cloud)
# ==============================================================================
qdrant_client = QdrantClient(
    url=os.getenv("QDRANT_URL"),
    api_key=os.getenv("QDRANT_API_KEY")
)

memory_config = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "client": qdrant_client,
            "collection_name": "aegistel_telecom_memory"
        }
    }
}
memory = Memory.from_config(memory_config)

# ==============================================================================
# 3. CREWAI SPECIALIST AGENTS
# ==============================================================================
fraud_agent = Agent(
    role="Fintech & Identity Fraud Specialist",
    goal="Identify account takeover risks using SIM Swap and Number Verification CAMARA APIs.",
    backstory="Ex-telecom fraud analyst specializing in 3GPP security signals and identity matching.",
    llm=llm_fast_action,
    verbose=True
)

qos_agent = Agent(
    role="5G Network Quality & Slicing Specialist",
    goal="Optimize latency and bandwidth allocations using Quality on Demand (QoD) and Device Reachability.",
    backstory="Expert 5G Core engineer experienced with PCF, NEF, and 5QI slice provisioning.",
    llm=llm_fast_action,
    verbose=True
)

risk_consensus_agent = Agent(
    role="Senior Policy & Risk Consensus Manager",
    goal="Synthesize telemetry from specialist agents and determine final ALLOW/BLOCK/ESCALATE actions.",
    backstory="Chief Information Security Officer synthesizing complex multi-agent signals into execution policies.",
    llm=llm_reasoning,
    verbose=True
)

# ==============================================================================
# 4. LANGGRAPH WORKFLOW STATE & NODES
# ==============================================================================
class AgentState(TypedDict):
    event_payload: Dict[str, Any]
    relevant_memories: List[str]
    specialist_findings: Dict[str, Any]
    final_decision: str
    execution_trace: List[str]

def recall_memory_node(state: AgentState) -> AgentState:
    user_id = state["event_payload"].get("msisdn", "unknown")
    past_memories = memory.search(query=f"Fraud and QoS history for {user_id}", user_id=user_id)
    
    state["relevant_memories"] = [m["text"] for m in past_memories.get("results", [])]
    state["execution_trace"].append(f"🧠 Memory Recalled: Found {len(state['relevant_memories'])} past events.")
    return state

def run_crew_node(state: AgentState) -> AgentState:
    t1 = Task(
        description=f"Analyze fraud risks for payload: {json.dumps(state['event_payload'])}",
        agent=fraud_agent,
        expected_output="JSON with SIM Swap status and identity confidence score."
    )
    t2 = Task(
        description=f"Assess network state and slice needs for payload: {json.dumps(state['event_payload'])}",
        agent=qos_agent,
        expected_output="JSON with 5QI slice requirement and congestion status."
    )
    t3 = Task(
        description=f"Synthesize findings and memories: {state['relevant_memories']}. Emit final decision.",
        agent=risk_consensus_agent,
        expected_output="Final Decision: ALLOW, BLOCK, or ESCALATE with justification."
    )

    crew = Crew(
        agents=[fraud_agent, qos_agent, risk_consensus_agent],
        tasks=[t1, t2, t3],
        process=Process.sequential
    )
    
    res = crew.kickoff()
    state["final_decision"] = str(res)
    state["execution_trace"].append("⚡ Specialist Crew Executed successfully.")
    
    # Store decision back to Mem0
    memory.add(
        f"Event: {state['event_payload'].get('event')} | Outcome: {state['final_decision']}",
        user_id=state["event_payload"].get("msisdn", "unknown")
    )
    return state

# Build Graph
builder = StateGraph(AgentState)
builder.add_node("RecallMemory", recall_memory_node)
builder.add_node("ExecuteCrew", run_crew_node)

builder.set_entry_point("RecallMemory")
builder.add_edge("RecallMemory", "ExecuteCrew")
builder.add_edge("ExecuteCrew", END)

aegistel_graph = builder.compile()