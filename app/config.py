from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    WA_PHONE_NUMBER_ID: str = ""
    WA_TOKEN: str = ""
    WA_VERIFY_TOKEN: str = ""
    WA_APP_SECRET: str = ""
    WA_API_VERSION: str = "v21.0"

    GEMINI_API_KEY: str = ""
    GEMINI_API_KEY_FALLBACK: str = ""  # a key from a separate Google account/project: its own quota
    GEMINI_MODEL: str = ""
    GEMINI_MODEL_FALLBACK: str = ""  # tried when GEMINI_MODEL is slow, overloaded or out of quota, before Groq
    GEMINI_EMBED_MODEL: str = ""

    ELEVENLABS_API_KEY: str = ""

    TAVILY_API_KEY: str = ""
    TAVILY_SEARCH_DEPTH: str = "fast"  # basic | fast | ultra-fast. fast is ~2x quicker and found better sources in a 4-claim A/B (2026-10-01)

    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    GROQ_WHISPER_MODEL: str = "whisper-large-v3"
    OPENROUTER_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""

    PHONE_HASH_SALT: str = ""
    PROMPT_GUARD_MODEL: str = "meta-llama/llama-prompt-guard-2-86m"
    PROMPT_GUARD_BLOCK_THRESHOLD: float = 0.5
    PROMPT_GUARD_FLAG_THRESHOLD: float = 0.05
    MAX_CLAIMS_PER_MESSAGE: int = 3

    SEMANTIC_MATCH_THRESHOLD: float = 0.90
    MAX_AUDIO_SECONDS: int = 180
    RATE_LIMIT_PER_HOUR: int = 20


settings = Settings()
