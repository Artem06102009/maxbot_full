import os
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

class Settings(BaseModel):
    DATABASE_URL: str = Field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "sqlite+aiosqlite:///data/liga.db"
        )
    )
    MAX_BOT_TOKEN: str = Field(
        default_factory=lambda: os.getenv("MAX_BOT_TOKEN", "")
    )
    MAX_API_BASE_URL: str = Field(
        default_factory=lambda: os.getenv(
            "MAX_API_BASE_URL", "https://platform-api2.max.ru"
        )
    )
    MAX_SSL_VERIFY: bool = Field(
        default_factory=lambda: os.getenv("MAX_SSL_VERIFY", "false").lower() in ("true", "1", "yes")
    )
    WEBAPP_BASE_URL: str = Field(
        default_factory=lambda: os.getenv("WEBAPP_BASE_URL", "https://itcubefor.ru")
    )
    CURFEW_START_HOUR: int = Field(
        default_factory=lambda: int(os.getenv("CURFEW_START_HOUR", "22"))
    )
    CURFEW_END_HOUR: int = Field(
        default_factory=lambda: int(os.getenv("CURFEW_END_HOUR", "7"))
    )
    HOST: str = Field(
        default_factory=lambda: os.getenv("HOST", "0.0.0.0")
    )
    PORT: int = Field(
        default_factory=lambda: int(os.getenv("PORT", "8000"))
    )

settings = Settings()

def get_settings() -> Settings:
    return settings
