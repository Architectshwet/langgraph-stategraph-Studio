import os

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.1")
OPENAI_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.1"))

POSTGRESQL_URL = os.getenv("POSTGRESQL_URL", "").strip()
ENABLE_SESSION_SYNC = os.getenv("ENABLE_SESSION_SYNC", "true").lower() == "true"
WAFER_SEED_RESET = os.getenv("WAFER_SEED_RESET", "false").lower() == "true"

LANGFUSE_ENABLED = os.getenv("LANGFUSE_ENABLED", "false").lower() == "true"
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8000")
