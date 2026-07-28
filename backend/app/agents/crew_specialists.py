from crewai import Agent, Task, Crew
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from app.core.config import settings
from app.services.aduna_service import aduna_client

# LLM Selection Pattern
groq_llm = ChatGroq(temperature=0, model_name="llama-3.3-70b-versatile", groq_api_key=settings.GROQ_API_KEY)
gemini_llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", google_api_key=settings.GOOGLE_API_KEY)

# Specialist Agents
security_guard_agent = Agent(
    role="Telco Security Specialist",
    goal="Verify subscriber line integrity, SIM Swap status, and identity vectors via CAMARA APIs.",
    backstory="Expert automated telecom fraud detection unit specializing in zero-trust carrier identity.",
    llm=groq_llm,
    verbose=True
)

network_qod_agent = Agent(
    role="5G QoS Orchestrator",
    goal="Evaluate dynamic bandwidth requirements and provision low-latency 5G network slices.",
    backstory="Autonomous 3GPP network manager optimizing 5QI parameters for critical application traffic.",
    llm=groq_llm,
    verbose=True
)

audit_risk_agent = Agent(
    role="Deep Reasoning Risk Auditor",
    goal="Perform contextual threat evaluation using long-term historical vector memory.",
    backstory="Enterprise security auditor powered by Gemini 2.5 Pro that correlates complex network events.",
    llm=gemini_llm,
    verbose=True
)