#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import logging
import asyncio
from flask import Flask, request
from openai import OpenAI

# NEW: Updated imports for python-telegram-bot v20+
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Конфигурация ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL")
MODEL_NAME = os.environ.get("MODEL_NAME", "gpt-4o")

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise ValueError("Необходимо установить переменные окружения: TELEGRAM_BOT_TOKEN и OPENAI_API_KEY")

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Инициализация клиентов ---
client_args = {"api_key": OPENAI_API_KEY}
if OPENAI_BASE_URL:
    client_args["base_url"] = OPENAI_BASE_URL
client = OpenAI(**client_args)

# NEW: Use Application.builder() to create the bot application
application = Application.builder().token(TELEGRAM_TOKEN).build()

app = Flask(__name__)

# --- Основная логика генерации кода ---

def extract_code_block(text: str) -> str:
    """Извлекает код из блока ```...``` или возвращает весь текст."""
    match = re.search(r"```[a-zA-Z0-9_+\-]*\n([\s\S]*?)```", text)
    return (match.group(1) if match else text).strip()

def generate_from_spec(model: str, spec: str, lang_hint: str = "python") -> str:
    """Генерирует код по текстовому описанию (промпту)."""
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
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )
        text_content = completion.choices[0].message.content
        return extract_code_block(text_content)
    except Exception as e:
        logger.error(f"Ошибка при вызове OpenAI API: {e}")
        return f"Произошла ошибка при генерации кода: {e}"

# --- Обработчики команд Telegram ---
# NEW: Handler functions are now async

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение при команде /start."""
    user = update.effective_user
    welcome_message = (
        f"Привет, {user.first_name}!\n\n"
        f"Я бот для генерации кода. Просто отправь мне текстовое описание (промпт), "
        f"и я сгенерирую для тебя код на Python, используя модель **{MODEL_NAME}**."
    )
    await update.message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает текстовые сообщения как промпты для генерации кода."""
    prompt = update.message.text
    chat_id = update.message.chat_id

    await context.bot.send_message(chat_id, text="⏳ Генерирую код по вашему запросу...")
    logger.info(f"Получен промпт от {update.effective_user.username}: {prompt}")

    generated_code = generate_from_spec(MODEL_NAME, prompt)

    try:
        await update.message.reply_text(f"```python\n{generated_code}\n```", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception:
        logger.warning("Не удалось отправить как Markdown. Отправляю как обычный текст.")
        await update.message.reply_text(generated_code)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логирует ошибки."""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# --- Настройка веб-хука и запуск Flask ---

@app.route('/webhook', methods=['POST'])
async def webhook():
    """Этот эндпоинт принимает обновления от Telegram и обрабатывает их."""
    update_data = request.get_json(force=True)
    update = Update.de_json(update_data, application.bot)
    
    # NEW: Process updates asynchronously
    await application.process_update(update)
    return 'ok'

@app.route('/')
def index():
    """Простая страница для проверки работоспособности сервиса."""
    return "I'm alive!"

# NEW: Handlers are added to the application object
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_error_handler(error_handler)

if __name__ == "__main__":
    # Этот блок теперь используется только для локального запуска
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
