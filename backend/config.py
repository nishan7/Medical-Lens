from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Env vars (no prefix):
    - NVIDIA_API_KEY
    - NVIDIA_MODEL (optional)
    """

    nvidia_api_key: str
    nvidia_model: str = "nvidia/nemotron-3-super-120b-a12b"
    nvidia_temperature: float = 0.7
    nvidia_top_p: float = 0.95
    nvidia_max_tokens: int = 2048
    nvidia_reasoning_budget: int = 2048
    search_max_rows: int = 10
    tool_cache_enabled: bool = False
    tool_cache_ttl_seconds: int = 3600
    tool_cache_path: str = ".cache/tool_cache.sqlite3"
    tool_cache_max_entries: int = 20000
    agent_recursion_limit: int = 8
    chat_request_timeout_seconds: float = 120.0
    ocr_enabled: bool = True
    ocr_max_pdf_pages: int = 3
    ocr_max_text_chars: int = 12000
    ocr_timeout_seconds: float = 60.0
    ocr_allowed_types: str = "image/jpeg,image/png,image/webp,image/jpg,application/pdf"

    class Config:
        env_file = ".env"


settings = Settings()
