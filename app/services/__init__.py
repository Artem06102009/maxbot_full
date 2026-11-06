from app.services.max_client import MaxBotClient, get_bot_client
from app.services.anti_cheat import AntiCheatService
from app.services.gamification import GamificationService
from app.services.teacher_service import TeacherService
from app.services.bot_service import BotService, handle_update
from app.services.auth import get_current_user, require_teacher

__all__ = [
    "MaxBotClient",
    "get_bot_client",
    "AntiCheatService",
    "GamificationService",
    "TeacherService",
    "BotService",
    "handle_update",
    "get_current_user",
    "require_teacher",
]

