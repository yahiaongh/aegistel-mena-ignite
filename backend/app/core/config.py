from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT_ENV_FILE), extra="allow")

    PROJECT_NAME: str = "AegisTel MENA Ignite"
    API_V1_STR: str = "/api/v1"

    NOKIA_NAC_API_KEY: str = ""
    NOKIA_NAC_HOST: str = "network-as-code.nokia.rapidapi.com"
    NOKIA_CAMARA_BASE_URL: str = "https://network-as-code.p-eu.rapidapi.com/passthrough/camara/v1"

    GROQ_API_KEY: str = ""
    LLM_MODEL: str = "groq/llama-3.3-70b-versatile"
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GOOGLE_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-flash-latest"
    OPENROUTER_API_KEY: str = ""
    CEREBRAS_API_KEY: str = ""
    DEEPGRAM_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    HF_TOKEN: str = ""
    LITELLM_DROP_PARAMS: bool = True
    APP_ENV: str = "development"
    PORT: int = 8000

    QDRANT_API_KEY: str = ""
    QDRANT_URL: str = "https://2ae71960-6905-453b-b4ef-e6d16d0ef69a.eu-central-1-0.aws.cloud.qdrant.io"


settings = Settings()