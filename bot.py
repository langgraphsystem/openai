#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import logging
import asyncio
import json
from flask import Flask, request
from openai import OpenAI

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# --- Configuration ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL")
MODEL_NAME = os.environ.get("MODEL_NAME", "gpt-4o")

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise ValueError("You must set the environment variables: TELEGRAM_BOT_TOKEN and OPENAI_API_KEY")

# --- Logging Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Client Initialization ---
client_args = {"api_key": OPENAI_API_KEY}
if OPENAI_BASE_URL:
    client_args["base_url"] = OPENAI_BASE_URL
client = OpenAI(**client_args)

application = Application.builder().token(TELEGRAM_TOKEN).build()
app = Flask(__name__)

# --- Core Code Generation Logic ---
def extract_code_block(text: str) -> str:
    """Extracts code from a ```...``` block or returns the entire text."""
    match = re.search(r"```[a-zA-Z0-9_+\-]*\n([\s\S]*?)```", text)
    return (match.group(1) if match else text).strip()

def generate_from_spec(model: str, spec: str, lang_hint: str = "python") -> str:
    """Generates code from a text description (prompt)."""
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
        logger.info(f"Sending request to OpenAI with model {model}...")
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )
        text_content = completion.choices[0].message.content
        logger.info("Successfully received response from OpenAI.")
        return extract_code_block(text_content)
    except Exception as e:
        logger.error(f"Critical error calling OpenAI API: {e}", exc_info=True)
        return f"An error occurred while generating the code: {e}"

# --- Telegram Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    logger.info(f"User {user.username} ({user.id}) triggered /start.")
    welcome_message = (
        f"Hello, {user.first_name}!\n\n"
        f"I am a code generation bot. Just send me a text description (prompt), "
        f"and I will generate Python code for you using the **{MODEL_NAME}** model."
    )
    await update.message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    prompt = update.message.text
    chat_id = update.message.chat_id
    
    logger.info(f"Received message from {user.username} ({user.id}) in chat {chat_id}.")
    
    await context.bot.send_message(chat_id, text="⏳ Generating code based on your request...")
    logger.info("Sent interim 'Generating code...' message.")

    generated_code = generate_from_spec(MODEL_NAME, prompt)

    try:
        logger.info("Attempting to send generated code as MarkdownV2.")
        await update.message.reply_text(f"```python\n{generated_code}\n```", parse_mode=ParseMode.MARKDOWN_V2)
        logger.info(f"Code successfully sent to chat {chat_id}.")
    except Exception as e:
        logger.warning(f"Failed to send as Markdown: {e}. Sending as plain text.")
        try:
            await update.message.reply_text(generated_code)
            logger.info(f"Code successfully sent to chat {chat_id} as plain text.")
        except Exception as e_text:
            logger.error(f"Failed to send code even as plain text: {e_text}", exc_info=True)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    if update and isinstance(update, Update):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Sorry, an internal error occurred."
        )

# --- Webhook Setup and Flask App ---
@app.route('/webhook', methods=['POST'])
def webhook() -> str:
    """Endpoint that accepts and processes updates from Telegram."""
    logger.info("Received incoming request to /webhook.")
    
    try:
        update_data = request.get_json(force=True)
    except Exception as e:
        logger.warning(f"Could not decode JSON from request: {e}")
        return 'ok'

    async def process_telegram_update():
        """Async function for proper initialization and handling."""
        await application.initialize()
        update = Update.de_json(update_data, application.bot)
        await application.process_update(update)
        await application.shutdown()

    try:
        asyncio.run(process_telegram_update())
        logger.info("Webhook request processed successfully.")
    except Exception as e:
        logger.error(f"Error during async processing: {e}", exc_info=True)
    
    return 'ok'

@app.route('/')
def index():
    return "I'm alive!"

# --- Registering Handlers ---
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_error_handler(error_handler)

# --- Starting the App ---
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting web server on port {port}...")
    app.run(host='0.0.0.0', port=port)
