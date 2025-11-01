import asyncio
from telegram.ext import Application, MessageHandler, filters
import os
import httpx
from models import UserRequest


TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8004")


async def handle_message(update, context):
    text = update.message.text or ""
    user_id = str(update.message.chat_id)
    payload = {"user_id": user_id, "text": text}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{ORCHESTRATOR_URL}/process", json=payload)
        data = resp.json()
        ans = data.get("answer")
        tpl = data.get("template")
        if ans:
            await update.message.reply_text(ans)
        else:
            if tpl == "blocked_pre_generation":
                await update.message.reply_text("Ваш запрос отклонён политикой безопасности.")
            elif tpl == "blocked_post_generation":
                await update.message.reply_text("Невозможно показать ответ по соображениям безопасности.")
            else:
                await update.message.reply_text("Произошла ошибка. Попробуйте позже.")


async def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())