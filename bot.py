#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import logging
import asyncio
import threading
from io import BytesIO
from typing import Dict, Any

from flask import Flask, request
from openai import OpenAI

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# =========================
# Configuration
# =========================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL")  # e.g. https://api.openai.com/v1
MODEL_NAME = os.environ.get("MODEL_NAME", "gpt-5")
PORT = int(os.environ.get("PORT", "5000"))

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise ValueError("You must set TELEGRAM_BOT_TOKEN and OPENAI_API_KEY env vars")

# =========================
# Logging
# =========================
logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("app")

# =========================
# OpenAI client (Responses API)
# =========================
client_args: Dict[str, Any] = {"api_key": OPENAI_API_KEY}
if OPENAI_BASE_URL:
    client_args["base_url"] = OPENAI_BASE_URL
client = OpenAI(**client_args)

# =========================
# Telegram Application (PTB)
# =========================
application = Application.builder().token(TELEGRAM_TOKEN).build()

# =========================
# Flask app
# =========================
app = Flask(__name__)

# =========================
# Helpers
# =========================

# Escape for MarkdownV2 (Telegram)
MDV2_ESCAPE_RE = re.compile(r'([_\*\[\]\(\)~`>#+\-=\|{}\.!])')
def escape_markdown_v2(text: str) -> str:
    return MDV2_ESCAPE_RE.sub(r'\\\1', text)

def extract_code_block(text: str) -> str:
    """
    Извлекает содержимое из ```...``` блока.
    Если блока нет — возвращает весь текст.
    """
    m = re.search(r"```[a-zA-Z0-9_+\-]*\n([\s\S]*?)```", text)
    return (m.group(1) if m else text).strip()

def generate_from_spec(model: str, spec: str, lang_hint: str = "python") -> str:
    """
    Генерация кода через Responses API (GPT-5).
    Возвращает ТОЛЬКО код (извлекается из fenced-блока).
    """
    system_prompt = (
        "You are a strict, production-grade code generator. "
        "Return only the code in a single fenced block. No explanations or any other text."
    )
    user_prompt = "\n".join([
        f"Language: {lang_hint}",
        "Task: Generate a complete, production-ready single code file strictly matching the spec below.",
        "Rules:",
        "- Return ONLY code in a single fenced block. No comments, no prose.",
        "- The code must be deterministic, self-contained, and require no external secrets.",
        "- If the spec omits details, pick sensible production defaults.",
        "Spec:", spec
    ])

    try:
        logger.info(f"OpenAI Responses API request: model={model}")
        # Используем Responses API; без temperature; контроль длины — max_output_tokens
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_output_tokens=4000,  # безопасная «кепка» вывода
        )
        text_content = (getattr(resp, "output_text", None) or "").strip()
        if not text_content and hasattr(resp, "output"):
            # на всякий случай извлечём вручную
            try:
                chunks = []
                for part in resp.output or []:
                    for c in (part.get("content") or []):
                        if c.get("type") in ("output_text", "text"):
                            chunks.append(c.get("text", ""))
                text_content = "".join(chunks).strip()
            except Exception:
                pass
        return extract_code_block(text_content) if text_content else ""
    except Exception as e:
        logger.error(f"OpenAI Responses API error: {e}", exc_info=True)
        return f"An error occurred while generating the code: {e}"

async def reply_code(update: Update, code: str, lang: str = "python"):
    """
    Пытается отправить код MarkdownV2-блоком.
    При ошибке форматирования — отправляет как файл.
    """
    block = f"```{lang}\n{code}\n```"
    try:
        await update.message.reply_text(
            escape_markdown_v2(block),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.warning(f"MarkdownV2 send failed: {e}. Sending as document...")
        buf = BytesIO(code.encode("utf-8"))
        buf.name = f"generated.{ 'py' if lang=='python' else lang }"
        await update.message.reply_document(
            buf,
            caption="Generated code"
        )

# =========================
# Telegram Handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info(f"/start by {user.id} @{user.username}")
    msg = (
        f"Hello, {user.first_name}!\n\n"
        f"Send me a text description (prompt), and I'll generate code using **{MODEL_NAME}**."
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id
    prompt = update.message.text or ""
    logger.info(f"Message from {user.id} in chat {chat_id}. Len={len(prompt)}")

    await context.bot.send_message(chat_id, text="⏳ Generating code based on your request...")

    code = generate_from_spec(MODEL_NAME, prompt, lang_hint="python")
    await reply_code(update, code, lang="python")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling update:", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Sorry, an internal error occurred."
            )
    except Exception:
        pass

# Регистрируем хендлеры
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_error_handler(error_handler)

# =========================
# Run PTB in background event loop
# =========================
_loop: asyncio.AbstractEventLoop | None = None
_runner_started = threading.Event()

async def _start_telegram_application():
    """Инициализация и запуск PTB-приложения (один раз)."""
    logger.info("Initializing Telegram application...")
    await application.initialize()
    await application.start()
    logger.info("Telegram application started.")
    _runner_started.set()
    while True:
        await asyncio.sleep(3600)

def start_runner_thread():
    """Стартует отдельный поток с event loop для PTB."""
    global _loop
    if _loop is not None:
        return
    def _runner():
        global _loop
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        _loop.create_task(_start_telegram_application())
        try:
            _loop.run_forever()
        finally:
            _loop.run_until_complete(application.stop())
            _loop.run_until_complete(application.shutdown())
            _loop.close()
    t = threading.Thread(target=_runner, name="PTBRunner", daemon=True)
    t.start()
    _runner_started.wait(timeout=15)

# =========================
# Flask routes
# =========================
@app.route("/", methods=["GET"])
def index():
    return "I'm alive!"

@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Принимает апдейты от Telegram и кладёт их в очередь PTB.
    Приложение PTB уже запущено в фоне, поэтому просто создаём Update и enqueue.
    """
    try:
        update_data = request.get_json(force=True)
    except Exception as e:
        logger.warning(f"Invalid JSON at /webhook: {e}")
        return "ok"

    if not _loop:
        logger.error("PTB loop is not running yet")
        return "ok"

    try:
        upd = Update.de_json(update_data, application.bot)
        fut = asyncio.run_coroutine_threadsafe(application.update_queue.put(upd), _loop)
        fut.result(timeout=3)
    except Exception as e:
        logger.error(f"Failed to enqueue update: {e}", exc_info=True)

    return "ok"

# =========================
# Entrypoint
# =========================
if __name__ == "__main__":
    logger.info("Starting PTB runner thread...")
    start_runner_thread()
    logger.info(f"Starting Flask on 0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT)