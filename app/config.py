from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    WA_PHONE_NUMBER_ID: str = ""
    WA_TOKEN: str = ""
    WA_VERIFY_TOKEN: str = ""
    WA_APP_SECRET: str = ""
    WA_API_VERSION: str = "v21.0"

    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = ""
    GEMINI_EMBED_MODEL: str = ""

    BHASHINI_USER_ID: str = ""
    BHASHINI_API_KEY: str = ""
    BHASHINI_PIPELINE_ID: str = ""

    GROQ_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""

    PHONE_HASH_SALT: str = ""
    SEMANTIC_MATCH_THRESHOLD: float = 0.90
    MAX_AUDIO_SECONDS: int = 180
    RATE_LIMIT_PER_HOUR: int = 20


settings = Settings()
