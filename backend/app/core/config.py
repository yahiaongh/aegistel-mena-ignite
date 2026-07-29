import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "AegisTel MENA Ignite"
    API_V1_STR: str = "/api/v1"
    
    # Nokia Network as Code Configuration
    NOKIA_NAC_API_KEY: str = os.getenv("NOKIA_NAC_API_KEY", "")
    NOKIA_NAC_HOST: str = os.getenv("NOKIA_NAC_HOST", "network-as-code.nokia.rapidapi.com")
    
    # LLM & Orchestration Settings
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "groq/llama-3.3-70b-versatile")
    
    class Config:
        env_file = ".env"
        extra = "allow"

settings = Settings()