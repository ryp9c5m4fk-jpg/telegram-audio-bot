import os, sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CallbackQueryHandler,
    CommandHandler, ContextTypes, filters
)
from PyPDF2 import PdfReader
import openai

# ====== ENV ======
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

# ====== DATABASE ======
db = sqlite3.connect("library.db", check_same_thread=False)
db.execute("""
CREATE TABLE IF NOT EXISTS books (
    user INTEGER,
    book_id INTEGER,
    title TEXT,
    position INTEGER
)
""")

# ====== MEMORY ======
mem = {}  # active book in RAM

# ====== HELPERS ======
def read_pdf(path):
    r = PdfReader(path); t=""
    for p in r.pages:
        if p.extract_text(): t += p.extract_text()
    return t

def split(text, size=2500):
    return [text[i:i+size] for i in range(0, len(text), size)]

def summarize(text):
    r = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":f"Кратко перескажи:\n{text}"}]
    )
    return r.choices[0].message.content

def tts(text):
    audio = openai.audio.speech.create(
        model="gpt-4o-mini-tts",
        voice="alloy",
        input=text
    )
    fn="audio.mp3"
    audio.stream_to_file(fn)
    return fn

def controls(paused):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏸ Пауза" if not paused else "▶️ Продолжить",
                                 callback_data="pause")
        ],
        [
            InlineKeyboardButton("▶️ Дальше", callback_data="next")
        ]
    ])

# ====== HANDLERS ======
async def new_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    chunks = split(update.message.text)
    cur = db.execute(
        "SELECT COUNT(*) FROM books WHERE user=?", (uid,)
    ).fetchone()[0]

    db.execute(
        "INSERT INTO books VALUES (?,?,?,?)",
        (uid, cur, "Текст", 0)
    )
    db.commit()

    mem[uid] = {
        "book": cur,
        "chunks": chunks,
        "pos": 0,
        "paused": False
    }
    await play(update, uid)

async def new_pdf(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    f = await update.message.document.get_file()
    path = "book.pdf"
    await f.download_to_drive(path)

    text = read_pdf(path)
    os.remove(path)
    chunks = split(text)

    cur = db.execute(
        "SELECT COUNT(*) FROM books WHERE user=?", (uid,)
    ).fetchone()[0]

    db.execute(
        "INSERT INTO books VALUES (?,?,?,?)",
        (uid, cur, update.message.document.file_name, 0)
    )
    db.commit()

    mem[uid] = {
        "book": cur,
        "chunks": chunks,
        "pos": 0,
        "paused": False
    }
    await play(update, uid)

async def play(update, uid):
    data = mem.get(uid)
    if not data or data["paused"]:
        return

    if data["pos"] >= len(data["chunks"]):
        await update.message.reply_text("📘 Книга закончилась")
        return

    text = summarize(data["chunks"][data["pos"]])
    audio = tts(text)

    await update.message.reply_voice(open(audio, "rb"))
    await update.message.reply_markup(controls(False))

    os.remove(audio)

async def buttons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()

    data = mem.get(uid)
    if not data:
        return

    if q.data == "pause":
        data["paused"] = not data["paused"]
        if not data["paused"]:
            await play(q.message, uid)
        else:
            await q.message.reply_text("⏸ Пауза")
        return

    if q.data == "next":
        data["pos"] += 1
        db.execute(
            "UPDATE books SET position=? WHERE user=? AND book_id=?",
            (data["pos"], uid, data["book"])
        )
        db.commit()
        await play(q.message, uid)

async def library(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rows = db.execute(
        "SELECT book_id, title, position FROM books WHERE user=?", (uid,)
    ).fetchall()

    if not rows:
        await update.message.reply_text("Библиотека пуста")
        return

    buttons = []
    for b, t, p in rows:
        buttons.append([
            InlineKeyboardButton(f"📘 {t} (стр. {p})",
                                 callback_data=f"open_{b}")
        ])

    await update.message.reply_text(
        "📚 Твоя библиотека:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def open_book(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()

    book_id = int(q.data.split("_")[1])
    row = db.execute(
        "SELECT position FROM books WHERE user=? AND book_id=?",
        (uid, book_id)
    ).fetchone()

    if not row:
        return

    mem[uid]["book"] = book_id
    mem[uid]["pos"] = row[0]
    mem[uid]["paused"] = False

    await play(q.message, uid)

# ====== RUN ======
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("library", library))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, new_text))
app.add_handler(MessageHandler(filters.Document.ALL, new_pdf))
app.add_handler(CallbackQueryHandler(open_book, pattern="^open_"))
app.add_handler(CallbackQueryHandler(buttons))
app.run_polling()