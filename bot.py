#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import logging
import asyncio
import json # <-- Импортируем для красивого вывода данных
from flask import Flask, request
from openai import OpenAI

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

# --- Настройка логирования (стало чуть подробнее) ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
# Уменьшим "шум" от сторонних библиотек, чтобы видеть только свои логи
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Инициализация клиентов ---
client_args = {"api_key": OPENAI_API_KEY}
if OPENAI_BASE_URL:
    client_args["base_url"] = OPENAI_BASE_URL
client = OpenAI(**client_args)

application = Application.builder().token(TELEGRAM_TOKEN).build()
app = Flask(__name__)

# --- Основная логика генерации кода ---

def extract_code_block(text: str) -> str:
    """Извлекает код из блока ```...``` или возвращает весь текст."""
    match = re.search(r"```[a-zA-Z0-9_+\-]*\n([\s\S]*?)```", text)
    return (match.group(1) if match else text).strip()

def generate_from_spec(model: str, spec: str, lang_hint: str = "python") -> str:
    """Генерирует код по текстовому описанию (промпту)."""
    # ... (prompts are the same)
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
        logger.info(f"Отправка запроса в OpenAI с моделью {model}...") # <-- НОВЫЙ ЛОГ
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )
        text_content = completion.choices[0].message.content
        logger.info("Успешно получен ответ от OpenAI.") # <-- НОВЫЙ ЛОГ
        return extract_code_block(text_content)
    except Exception as e:
        logger.error(f"Критическая ошибка при вызове OpenAI API: {e}", exc_info=True) # <-- УЛУЧШЕННЫЙ ЛОГ
        return f"Произошла ошибка при генерации кода: {e}"

# --- Обработчики команд Telegram ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info(f"Пользователь {user.username} ({user.id}) вызвал команду /start.") # <-- НОВЫЙ ЛОГ
    welcome_message = (
        f"Привет, {user.first_name}!\n\n"
        f"Я бот для генерации кода. Просто отправь мне текстовое описание (промпт), "
        f"и я сгенерирую для тебя код на Python, используя модель **{MODEL_NAME}**."
    )
    await update.message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    prompt = update.message.text
    chat_id = update.message.chat_id
    
    logger.info(f"Получено сообщение от {user.username} ({user.id}) в чате {chat_id}.") # <-- НОВЫЙ ЛОГ
    logger.debug(f"Текст сообщения: {prompt}") # <-- НОВЫЙ ЛОГ (уровень debug)

    await context.bot.send_message(chat_id, text="⏳ Генерирую код по вашему запросу...")
    logger.info("Отправлено промежуточное сообщение 'Генерирую код...'.") # <-- НОВЫЙ ЛОГ

    # Вызываем основную функцию
    generated_code = generate_from_spec(MODEL_NAME, prompt)

    # Попытка отправить результат
    try:
        logger.info("Пытаюсь отправить сгенерированный код как MarkdownV2.") # <-- НОВЫЙ ЛОГ
        await update.message.reply_text(f"```python\n{generated_code}\n```", parse_mode=ParseMode.MARKDOWN_V2)
        logger.info(f"Код успешно отправлен в чат {chat_id}.") # <-- НОВЫЙ ЛОГ
    except Exception as e:
        logger.warning(f"Не удалось отправить как Markdown: {e}. Отправляю как обычный текст.") # <-- УЛУЧШЕННЫЙ ЛОГ
        try:
            await update.message.reply_text(generated_code)
            logger.info(f"Код успешно отправлен в чат {chat_id} как обычный текст.") # <-- НОВЫЙ ЛОГ
        except Exception as e_text:
            logger.error(f"Не удалось отправить код даже как обычный текст: {e_text}", exc_info=True) # <-- НОВЫЙ ЛОГ

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # <-- УЛУЧШЕННЫЙ ОБРАБОТЧИК ОШИБОК
    logger.error(msg="Произошло исключение при обработке обновления:", exc_info=context.error)
    if update and isinstance(update, Update):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Извините, произошла внутренняя ошибка. Я уже сообщил разработчикам."
        )


# --- Настройка веб-хука и запуск Flask ---

@app.route('/webhook', methods=['POST'])
def webhook() -> str:
    update_data = request.get_json(force=True)
    # <-- НОВЫЙ ЛОГ: смотрим, что именно прислал Telegram
    logger.info(f"Получен входящий запрос на /webhook.")
    logger.debug(f"Тело запроса: {json.dumps(update_data, indent=2, ensure_ascii=False)}")
    
    update = Update.de_json(update_data, application.bot)
    
    # Запускаем асинхронную обработку
    asyncio.run(application.process_update(update))
    
    logger.info("Запрос на /webhook успешно обработан.") # <-- НОВЫЙ ЛОГ
    return 'ok'

@app.route('/')
def index():
    # logger.info("Проверка статуса на эндпоинте / - I'm alive!") # Можно раскомментировать для отладки
    return "I'm alive!"

application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_error_handler(error_handler)

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Запуск веб-сервера на порту {port}...") # <-- НОВЫЙ ЛОГ
    app.run(host='0.0.0.0', port=port)

