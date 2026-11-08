import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from maxapi import Bot
from maxapi.types import BotCommand

from app.config import get_settings
from app.database.session import init_db
from app.services.bot_service import dp, sync_bot_chats
from app.api.webapp import router as webapp_router

settings = get_settings()

bot = None
if settings.MAX_BOT_TOKEN:
    bot = Bot(token=settings.MAX_BOT_TOKEN)

async def register_bot_commands(target_bot: Bot):
    try:
        commands = [
            BotCommand(name="ask", description="Задать вопрос классу"),
            BotCommand(name="start", description="Главное меню и профиль"),
            BotCommand(name="help", description="Инструкция по взаимопомощи"),
            BotCommand(name="manage", description="Кабинет учителя"),
        ]
        await target_bot.set_commands(*commands)
        print("[БОТ] Команды бота (/ask, /start, /help, /manage) зарегистрированы в MAX API")
    except Exception as e:
        print(f"[БОТ] Регистрация команд: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    bot_task = None
    if bot and settings.MAX_BOT_TOKEN:
        await register_bot_commands(bot)
        await sync_bot_chats(bot)
        print("[БОТ] Запуск опроса MAX API (библиотека maxapi)...")
        bot_task = asyncio.create_task(dp.start_polling(bot))
    yield
    if bot:
        print("[БОТ] Остановка опроса MAX API...")
        await dp.stop_polling()
    if bot_task:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass

app = FastAPI(
    title="Лига Помощников",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webapp_router, prefix="/api")
app.include_router(webapp_router, prefix="/api/v1/webapp")

frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")

@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})

@app.get("/")
@app.get("/webapp")
async def serve_index():
    index_file = os.path.join(frontend_dir, "index.html")
    return FileResponse(index_file)

if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
