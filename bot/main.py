from maxapi import Bot, Dispatcher
from config import BOT_TOKEN

bot = Bot(token=BOT_TOKEN or "demo_token")
dp = Dispatcher()