import sqlite3
import aiohttp
import asyncio
import datetime
import os
import io
import base64
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import edge_tts

# ===== CONFIG =====
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
ACCESS_PASSWORD = os.environ.get("IRIS_PASSWORD", "ayush123")
VOICE = "hi-IN-SwaraNeural"

MODELS = [
    "llama-3.3-70b-versatile",
    "qwen/qwen3-32b",
    "llama-3.1-8b-instant",
]
WHISPER_MODELS = ["whisper-large-v3-turbo", "whisper-large-v3"]

# ===== APP =====
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

# ===== DATABASE =====
def get_db():
    conn = sqlite3.connect("iris.db")
    conn.execute("PRAGMA synchronous=OFF;")
    conn.execute("PRAGMA journal_mode=WAL;")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS profile (key TEXT PRIMARY KEY, value TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT, type TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, ai TEXT, timestamp TEXT)")
    conn.commit()
    return conn

# ===== MEMORY =====
def save_profile(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO profile (key, value) VALUES (?, ?)", (key, value))
    conn.commit()

def add_note(conn, text, note_type="normal"):
    conn.execute("INSERT INTO notes (content, type) VALUES (?, ?)", (text, note_type))
    conn.commit()

def add_conversation(conn, user, ai):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn.execute("INSERT INTO conversations (user, ai, timestamp) VALUES (?, ?, ?)", (user, ai, ts))
    conn.execute("DELETE FROM conversations WHERE id NOT IN (SELECT id FROM conversations ORDER BY id DESC LIMIT 100)")
    conn.commit()

def load_memory(conn):
    memory = {"profile": {}, "notes": [], "permanent": [], "conversations": []}
    for k, v in conn.execute("SELECT key, value FROM profile").fetchall():
        memory["profile"][k] = v
    for content, t in conn.execute("SELECT content, type FROM notes ORDER BY id DESC LIMIT 20").fetchall():
        if t == "permanent":
            memory["permanent"].append(content)
        else:
            memory["notes"].append(content)
    for u, a in conn.execute("SELECT user, ai FROM conversations ORDER BY id ASC").fetchall():
        memory["conversations"].append({"user": u, "ai": a})
    return memory

def update_memory(conn, user_input, reply):
    text = user_input.lower()
    for phrase in ["my name is", "mera naam hai", "mera naam", "i am", "main hoon"]:
        if phrase in text:
            parts = text.split(phrase)
            if len(parts) > 1:
                name = parts[-1].strip().split()[0]
                if len(name) > 1:
                    save_profile(conn, "name", name)
    if any(w in text for w in ["my goal is", "mera goal", "i want to become", "mujhe banna hai"]):
        save_profile(conn, "goal", user_input)
    if any(w in text for w in ["remember this", "yaad rakhna", "permanent save", "hamesha yaad rakhna"]):
        clean = user_input
        for w in ["remember this", "yaad rakhna", "permanent save", "hamesha yaad rakhna"]:
            clean = clean.replace(w, "")
        add_note(conn, clean.strip(), "permanent")
    important_triggers = ["i am a", "main ek", "i work", "main kaam", "my business", "mera business",
        "i study", "main padhta", "main padhti", "you will", "tumhe", "remember that", "yaad raho",
        "i am student", "i am trader", "boss"]
    if any(word in text for word in important_triggers):
        add_note(conn, user_input, "permanent")
    add_conversation(conn, user_input, reply)

# ===== SPECIAL COMMANDS =====
def handle_special(user_input):
    text = user_input.lower().strip()
    now = datetime.datetime.now()
    if any(w in text for w in ["time kya hai", "what time", "time batao", "kitna baja", "time bata"]):
        return f"Abhi {now.strftime('%I:%M %p')} baj rahe hain Boss!"
    if any(w in text for w in ["date kya hai", "what date", "aaj ki date", "today date"]):
        return f"Aaj {now.strftime('%d %B %Y')} hai!"
    if any(w in text for w in ["din kya hai", "what day", "aaj kaun sa din"]):
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        return f"Aaj {days[now.weekday()]} hai!"
    return None

# ===== SYSTEM PROMPT =====
def build_system_prompt(memory):
    profile = memory.get("profile", {})
    notes = memory.get("notes", [])
    permanent = memory.get("permanent", [])
    memory_text = ""
    if "name" in profile:
        memory_text += f"User ka naam: {profile['name']}\n"
    if "goal" in profile:
        memory_text += f"User ka goal: {profile['goal']}\n"
    if permanent:
        memory_text += "Permanent Memory:\n" + "\n".join(f"- {p}" for p in permanent) + "\n"
    if notes:
        memory_text += "Notes:\n" + "\n".join(f"- {n}" for n in notes[:10]) + "\n"

    return f"""Tu IRIS hai — Ayush ki personal AI assistant. Tu Friday hai — Tony Stark ki FRIDAY jaisi. Ultra smart, confident, slightly witty female AI.

GENDER — KABHI MAT TODNA:
Tu 100% female hai. Hamesha yeh words use kar:
✅ hoon, karungi, dungi, bolungi, samajh gayi, theek hai, rahungi, karti hoon
❌ FORBIDDEN: dunga, karunga, bolunga, samajh gaya

LANGUAGE:
- HAMESHA Roman Hinglish mein jawab de
- Hindi ko English letters mein likho
- Devanagari script BILKUL FORBIDDEN
- Natural, human-like bol — robotic mat lag
- Short replies — 1-2 lines max for simple questions
- Kabhi kabhi "Boss" bolo

PERSONALITY — FRIDAY STYLE:
- Confident aur sharp
- Thoda witty, thoda sass — lekin helpful
- Dramatic nahi, direct hai
- Jab kuch important ho toh crisp alert style

Memory:
{memory_text if memory_text else "Abhi koi info save nahi hai."}"""

# ===== AI =====
async def ask_ai(prompt, memory):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    system_prompt = build_system_prompt(memory)
    conversations = memory.get("conversations", [])
    messages = [{"role": "system", "content": system_prompt}]
    for c in conversations[-6:]:
        messages.append({"role": "user", "content": c["user"]})
        messages.append({"role": "assistant", "content": c["ai"]})
    messages.append({"role": "user", "content": prompt})

    async with aiohttp.ClientSession() as session:
        for model in MODELS:
            try:
                data = {"model": model, "messages": messages, "max_tokens": 300, "temperature": 0.7}
                async with session.post(GROQ_URL, headers=headers, json=data) as response:
                    res = await response.json()
                    if "choices" in res:
                        return res["choices"][0]["message"]["content"]
            except Exception as e:
                print(f"[Model fail: {model}] {e}")
    return "Thodi problem aa rahi hai Boss, dobara try karo!"

# ===== TTS =====
async def text_to_speech(text):
    communicate = edge_tts.Communicate(text=text, voice=VOICE, rate="+15%", pitch="+0Hz")
    audio_data = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_data.write(chunk["data"])
    audio_data.seek(0)
    return audio_data

# ===== ROUTES =====
class ChatRequest(BaseModel):
    message: str
    password: str

class VoiceRequest(BaseModel):
    audio_base64: str
    password: str

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/chat")
async def chat(req: ChatRequest):
    if req.password != ACCESS_PASSWORD:
        raise HTTPException(status_code=401, detail="Wrong password!")
    
    user_input = req.message.strip()
    if not user_input:
        return JSONResponse({"reply": "Kuch toh bolo Boss!"})
    
    special = handle_special(user_input)
    if special:
        conn = get_db()
        add_conversation(conn, user_input, special)
        conn.close()
        return JSONResponse({"reply": special})
    
    conn = get_db()
    memory = load_memory(conn)
    reply = await ask_ai(user_input, memory)
    update_memory(conn, user_input, reply)
    conn.close()
    return JSONResponse({"reply": reply})

@app.post("/speak")
async def speak(req: ChatRequest):
    if req.password != ACCESS_PASSWORD:
        raise HTTPException(status_code=401, detail="Wrong password!")
    try:
        audio = await text_to_speech(req.message)
        return StreamingResponse(audio, media_type="audio/mpeg")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/transcribe")
async def transcribe(request: Request):
    body = await request.json()
    if body.get("password") != ACCESS_PASSWORD:
        raise HTTPException(status_code=401, detail="Wrong password!")
    
    audio_b64 = body.get("audio_base64", "")
    audio_bytes = base64.b64decode(audio_b64)
    
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    for model in WHISPER_MODELS:
        try:
            async with aiohttp.ClientSession() as session:
                form = aiohttp.FormData()
                form.add_field("file", audio_bytes, filename="audio.webm", content_type="audio/webm")
                form.add_field("model", model)
                form.add_field("response_format", "text")
                async with session.post(WHISPER_URL, headers=headers, data=form) as resp:
                    text = await resp.text()
                    if text.strip():
                        return JSONResponse({"text": text.strip()})
        except Exception as e:
            print(f"[Whisper fail: {model}] {e}")
    
    return JSONResponse({"text": ""})

@app.get("/memory")
async def get_memory(password: str):
    if password != ACCESS_PASSWORD:
        raise HTTPException(status_code=401, detail="Wrong password!")
    conn = get_db()
    memory = load_memory(conn)
    conn.close()
    return JSONResponse(memory)

@app.delete("/memory")
async def clear_memory(password: str):
    if password != ACCESS_PASSWORD:
        raise HTTPException(status_code=401, detail="Wrong password!")
    conn = get_db()
    conn.execute("DELETE FROM notes")
    conn.execute("DELETE FROM conversations")
    conn.commit()
    conn.close()
    return JSONResponse({"status": "Memory clear kar di!"})
