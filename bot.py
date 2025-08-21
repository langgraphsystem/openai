#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import logging
from flask import Flask, request
from openai import OpenAI
from telegram import Update, Bot
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters, CallbackContext
from telegram.constants import ParseMode

# --- Конфигурация ---
# Получаем токены из переменных окружения (безопасный способ для Railway)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL") # Необязательно
MODEL_NAME = os.environ.get("MODEL_NAME", "gpt-4o") # Модель по умолчанию

# Проверяем, что все необходимые переменные окружения установлены
if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise ValueError("Необходимо установить переменные окружения: TELEGRAM_BOT_TOKEN и OPENAI_API_KEY")

# Настройка логирования для отладки
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Инициализация клиентов ---
# Инициализация клиента OpenAI
client_args = {"api_key": OPENAI_API_KEY}
if OPENAI_BASE_URL:
    client_args["base_url"] = OPENAI_BASE_URL
client = OpenAI(**client_args)

# Инициализация Flask приложения и Telegram бота
app = Flask(__name__)
bot = Bot(token=TELEGRAM_TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

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
        # Используем новый стандартный API client.chat.completions.create
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1, # Низкая температура для более предсказуемого кода
        )
        text_content = completion.choices[0].message.content
        return extract_code_block(text_content)
    except Exception as e:
        logger.error(f"Ошибка при вызове OpenAI API: {e}")
        return f"Произошла ошибка при генерации кода: {e}"

# --- Обработчики команд Telegram ---

def start(update: Update, context: CallbackContext) -> None:
    """Отправляет приветственное сообщение при команде /start."""
    user = update.effective_user
    welcome_message = (
        f"Привет, {user.first_name}!\n\n"
        f"Я бот для генерации кода. Просто отправь мне текстовое описание (промпт), "
        f"и я сгенерирую для тебя код на Python, используя модель **{MODEL_NAME}**.\n\n"
        "Например, отправь мне: `веб-сервер на Flask с одной страницей, который выводит 'Hello, World!'`"
    )
    update.message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)

def handle_message(update: Update, context: CallbackContext) -> None:
    """Обрабатывает текстовые сообщения как промпты для генерации кода."""
    prompt = update.message.text
    chat_id = update.message.chat_id

    # Сообщение о том, что запрос принят
    context.bot.send_message(chat_id, text="⏳ Генерирую код по вашему запросу... Это может занять некоторое время.")

    logger.info(f"Получен промпт от {update.effective_user.username}: {prompt}")

    # Генерация кода
    generated_code = generate_from_spec(MODEL_NAME, prompt)

    # Отправка результата
    try:
        # Пытаемся отправить как отформатированный блок кода
        update.message.reply_text(f"```python\n{generated_code}\n```", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.warning(f"Не удалось отправить как Markdown: {e}. Отправляю как обычный текст.")
        # Если не получилось (например, из-за спецсимволов), отправляем как простой текст
        update.message.reply_text(generated_code)

def error_handler(update: object, context: CallbackContext) -> None:
    """Логирует ошибки и отправляет сообщение пользователю."""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    # Попытка уведомить пользователя об ошибке
    if isinstance(update, Update):
        update.message.reply_text("Произошла внутренняя ошибка. Попробуйте позже.")


# --- Настройка веб-хука и запуск Flask ---

@app.route('/webhook', methods=['POST'])
def webhook():
    """Этот эндпоинт принимает обновления от Telegram."""
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return 'ok'

@app.route('/')
def index():
    """Простая страница для проверки работоспособности сервиса."""
    return "I'm alive!"

if __name__ == "__main__":
    # Регистрация обработчиков
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    dispatcher.add_error_handler(error_handler)

    # Установка веб-хука. URL должен быть публичным (Railway предоставит его)
    # ВАЖНО: Замените YOUR_RAILWAY_APP_URL на ваш реальный URL от Railway
    # Например: https://my-cool-bot.up.railway.app
    # Этот код нужно запустить один раз локально или через CLI Railway для установки хука
    
    # --- ЛОКАЛЬНЫЙ ЗАПУСК ДЛЯ ТЕСТИРОВАНИЯ ---
    # Чтобы запустить локально:
    # 1. Создайте файл .env с TELEGRAM_BOT_TOKEN и OPENAI_API_KEY
    # 2. Установите ngrok: https://ngrok.com/
    # 3. Запустите ngrok: ngrok http 5000
    # 4. Скопируйте https URL от ngrok и установите веб-хук, раскомментировав строку ниже
    # 5. Запустите этот скрипт: python bot.py
    
    # bot.set_webhook("YOUR_NGROK_OR_RAILWAY_URL/webhook")
    
    # Запуск веб-сервера. Railway автоматически подставит нужный порт.
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)


