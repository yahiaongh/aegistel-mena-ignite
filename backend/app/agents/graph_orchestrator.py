# app/agents/graph_orchestrator.py
import os
import json
from typing import Annotated, TypedDict, List, Literal
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.agents.tools import check_sim_swap, verify_location
from app.schemas.telemetry import AuditRequest, AuditResponse, AgentTraceItem

load_dotenv()

groq_api_key = os.getenv("GROQ_API_KEY")
if not groq_api_key:
    raise ValueError("GROQ_API_KEY is missing from environment variables.")

class FinalAssessment(BaseModel):
    status: Literal["APPROVED", "REJECTED", "MANUAL_REVIEW"] = Field(
        ..., description="Final decision: APPROVED if safe, REJECTED if fraudulent, MANUAL_REVIEW if ambiguous or high risk."
    )
    risk_score: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = Field(
        ..., description="Assessed threat level based on telemetry and transaction profile."
    )
    sim_swap_cleared: bool = Field(
        ..., description="True if no recent SIM swap occurred (swapped=False), False if a SIM swap was detected."
    )
    location_cleared: bool = Field(
        ..., description="True if location check returned TRUE, False if location check failed."
    )
    reasoning: str = Field(
        ..., description="Comprehensive narrative explanation behind the fraud decision."
    )

class AuditState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    assessment: FinalAssessment | None

tools = [check_sim_swap, verify_location]

llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0.0,
    groq_api_key=groq_api_key
)

llm_with_tools = llm.bind_tools(tools)
structured_evaluator = llm.with_structured_output(FinalAssessment)

SYSTEM_PROMPT = """You are AegisTel's Autonomous Telco Fraud Detection Orchestrator.
Your goal is to evaluate financial transaction risks by autonomously querying real-time network telemetry via Nokia Network as Code (CAMARA) tools.

RULES:
1. Determine which tools (`check_sim_swap`, `verify_location`) are necessary to verify the device identity and location context.
2. Call tools dynamically to retrieve ground truth network telemetry.
3. Once telemetry collection is complete, finish tool calling so the evaluator can synthesize the final verdict.
"""

def agent_node(state: AuditState):
    messages = state["messages"]
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}

def evaluator_node(state: AuditState):
    messages = state["messages"]
    eval_prompt = [
        SystemMessage(content="You are the AegisTel Senior Risk Evaluator. Analyze the full investigation transcript and tool telemetry outputs above, then return the structured final verdict."),
        *messages
    ]
    assessment: FinalAssessment = structured_evaluator.invoke(eval_prompt)
    return {"assessment": assessment}

builder = StateGraph(AuditState)
builder.add_node("agent", agent_node)
builder.add_node("tools", ToolNode(tools))
builder.add_node("evaluator", evaluator_node)

builder.add_edge(START, "agent")

def route_after_agent(state: AuditState):
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "tools"
    return "evaluator"

builder.add_conditional_edges("agent", route_after_agent, {"tools": "tools", "evaluator": "evaluator"})
builder.add_edge("tools", "agent")
builder.add_edge("evaluator", END)

orchestrator = builder.compile()

async def execute_audit(request: AuditRequest) -> AuditResponse:
    initial_input = {
        "messages": [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=(
                f"Audit request received:\n"
                f"- MSISDN: {request.msisdn}\n"
                f"- Transaction Type: {request.transaction_type}\n"
                f"- Amount: ${request.amount:,.2f}\n"
                f"- Location: Lat {request.location.latitude}, Lon {request.location.longitude}"
            ))
        ]
    }
    
    final_state = await orchestrator.ainvoke(initial_input)
    messages = final_state["messages"]
    assessment: FinalAssessment = final_state["assessment"]
    
    trace: List[AgentTraceItem] = []
    
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                trace.append(AgentTraceItem(
                    agent="LLM_Risk_Orchestrator",
                    action=tc["name"].upper(),
                    thought="Executing autonomous tool selection based on risk context...",
                    status="COMPLETED",  # Changed from EXECUTING to mark historical completion
                    detail=str(tc["args"])
                ))
        elif msg.type == "tool":
            status = "FAILED"
            try:
                data = json.loads(msg.content)
                if data.get("swapped") is False or str(data.get("verificationResult")).upper() == "TRUE":
                    status = "PASSED"
            except Exception:
                pass

            trace.append(AgentTraceItem(
                agent="Nokia_CAMARA_Tool",
                action="TOOL_RESULT",
                thought="Received network response from Nokia API.",
                status=status,
                detail=msg.content
            ))

    return AuditResponse(
        msisdn=request.msisdn,
        transaction_type=request.transaction_type,
        amount=request.amount,
        location=request.location,
        sim_swap_cleared=assessment.sim_swap_cleared,
        location_cleared=assessment.location_cleared,
        risk_score=assessment.risk_score,
        status=assessment.status,
        reasoning=assessment.reasoning,
        agent_trace=trace
    )