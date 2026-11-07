import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from sqlalchemy import select
from app.database.session import async_session_factory, init_db
from app.database.models import User
from app.services.parent_report import parent_report_html, parent_week_payload
from app.services.max_client import get_bot_client
from app.services.bot_service import handle_update
from app.services.jobs import class_jobs_loop
from app.api.webapp import router as webapp_router

settings = get_settings()

async def run_bot_polling():
    client = get_bot_client()
    if not client.token:
        return

    marker = None
    while True:
        try:
            data = await client.get_updates(limit=100, timeout=30, marker=marker)
            new_marker = data.get("marker")
            if new_marker is not None:
                marker = new_marker
            for update in data.get("updates", []):
                try:
                    await handle_update(update, client=client)
                except Exception:
                    pass
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(2)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    bot_task = None
    jobs_task = asyncio.create_task(class_jobs_loop())
    if settings.MAX_BOT_TOKEN:
        bot_task = asyncio.create_task(run_bot_polling())
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


@app.get("/p/{token}")
async def parent_week_page(token: str):
    async with async_session_factory() as session:
        user = (await session.execute(select(User).where(User.report_token == token))).scalar_one_or_none()
        if not user:
            return HTMLResponse(
                "<html><body style='font-family:sans-serif;padding:40px'>Ссылка недействительна.</body></html>",
                status_code=404,
            )
        return HTMLResponse(parent_report_html(await parent_week_payload(session, user)))


if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
