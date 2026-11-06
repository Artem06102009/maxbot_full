import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select

from config import BOT_TOKEN, HOST, PORT
from app.database import create_db_and_tables, engine
from app.models import User
from app.reports import parent_report_html, parent_week_payload
from app.jobs import class_jobs_loop
from app.routes import api_router
from bot.main import bot, dp
import bot.handlers

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()

    bot_task = None
    jobs_task = asyncio.create_task(class_jobs_loop())
    if BOT_TOKEN and BOT_TOKEN != "YOUR_MAX_BOT_TOKEN_HERE" and BOT_TOKEN != "demo_token":
        try:
            bot_task = asyncio.create_task(dp.start_polling(bot))
            logger.info("Бот MAX запущен в фоновом режиме.")
        except Exception as e:
            logger.error(f"Не удалось подключиться к MAX API: {e}")

    yield

    jobs_task.cancel()
    if bot_task:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass
    try:
        await jobs_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Лига Помощников — MAX WebApp & Bot",
    version="1.0.0",
    lifespan=lifespan
)

app.include_router(api_router, prefix="/api")

@app.get("/app")
def app_redirect():
    return RedirectResponse(url="/")


@app.get("/p/{token}")
def parent_week_page(token: str):
    with Session(engine) as session:
        user = session.exec(select(User).where(User.report_token == token)).first()
        if not user:
            return HTMLResponse(
                "<html><body style='font-family:sans-serif;padding:40px'>Ссылка недействительна.</body></html>",
                status_code=404,
            )
        return HTMLResponse(parent_report_html(parent_week_payload(session, user)))

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
