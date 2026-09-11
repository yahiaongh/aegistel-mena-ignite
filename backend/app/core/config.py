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
    LLM_MODEL: str = "groq/openai/gpt-oss-120b"
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    GOOGLE_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-flash-latest"
    OPENROUTER_API_KEY: str = ""
    CEREBRAS_API_KEY: str = ""
    DEEPGRAM_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    HF_TOKEN: str = ""
    AEGISTEL_ADMIN_KEY: str = ""
    AEGISTEL_API_RATE_LIMIT_PER_MIN: int = 120
    AEGISTEL_DEFAULT_TENANT: str = "demo"
    # Comma-separated "tenant_id=api_key" pairs (e.g. "bank-a=k1,bank-b=k2").
    # When a client authenticates with one of these keys, the tenant namespace is
    # derived server-side from the credential and is used to scope memory writes
    # and reads. Tenant names are NEVER taken from the audit JSON body.
    AEGISTEL_TENANT_API_KEYS: str = ""
    # Anonymous callers (no tenant key) are scoped to AEGISTEL_DEFAULT_TENANT
    # only. Set this to false in production to require a tenant key for every audit.
    AEGISTEL_ALLOW_ANON_AUDIT: bool = True
    # Bank policy flag: provisioning a QoD session borrows a chargeable shared
    # network resource, so the audit decision NEVER provisions one. A session is
    # only created through POST /api/v1/audit/qod/provision (explicit confirmed
    # action) AND this flag. Default ON = QoD provisioning enabled with policy.
    AEGISTEL_QOD_POLICY_ENABLED: bool = True
    # QoD sessions are provisioned only for this server-side application
    # endpoint. Clients must never select the network destination.
    AEGISTEL_QOD_SERVICE_IP: str = "233.252.0.2"
    LITELLM_DROP_PARAMS: bool = True
    APP_ENV: str = "development"
    PORT: int = 8000

    QDRANT_API_KEY: str = ""
    QDRANT_URL: str = "https://2ae71960-6905-453b-b4ef-e6d16d0ef69a.eu-central-1-0.aws.cloud.qdrant.io"


settings = Settings()
