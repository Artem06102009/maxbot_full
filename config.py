import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BASE_URL = os.getenv("BASE_URL", "https://itcubefor.ru")
BOT_USERNAME = os.getenv("BOT_USERNAME", "ideya_fiks_59_bot")
SECRET_TEACHER_CODE = os.getenv("SECRET_TEACHER_CODE", "учитель11")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///database.db")
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS", "")
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat-2-Pro")
GIGACHAT_OAUTH_URL = os.getenv(
    "GIGACHAT_OAUTH_URL",
    "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
)
GIGACHAT_API_URL = os.getenv("GIGACHAT_API_URL", "https://api.giga.chat/v1")
HINT_DAILY_LIMIT = int(os.getenv("HINT_DAILY_LIMIT", "3"))
UNANSWERED_MINUTES = int(os.getenv("UNANSWERED_MINUTES", "30"))
