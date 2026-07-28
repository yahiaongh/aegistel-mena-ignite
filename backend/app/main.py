from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.websocket_router import router as ws_router

app = FastAPI(title="AegisTel Multi-Agent Telco Guard", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ws_router)

@app.get("/")
def health_check():
    return {"status": "ONLINE", "stack": "LangGraph + CrewAI + Gemini 2.5 Pro + Groq + Qdrant"}