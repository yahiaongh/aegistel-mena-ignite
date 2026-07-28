import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "AegisTel Multi-Agent Engine"
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    QDRANT_URL: str = os.getenv("QDRANT_URL", "https://your-cluster.qdrant.tech")
    QDRANT_API_KEY: str = os.getenv("QDRANT_API_KEY", "")
    ADUNA_API_KEY: str = os.getenv("ADUNA_API_KEY", "")
    NOKIA_NAC_API_KEY: str = os.getenv("NOKIA_NAC_API_KEY", "")
    
    class Config:
        env_file = ".env"

settings = Settings()