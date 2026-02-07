import os
import re
import asyncio
import aiohttp
import logging
import time
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant, MessageIdInvalid, FloodWait, PeerIdInvalid
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from aiohttp import web

# ================= CONFIGURATION =================
def get_clean_var(key, default=""):
    val = os.environ.get(key, default)
    return str(val).strip()

API_ID = int(get_clean_var("API_ID", "0"))
API_HASH = get_clean_var("API_HASH", "")
BOT_TOKEN = get_clean_var("BOT_TOKEN", "")
MONGO_URL = get_clean_var("MONGO_URL", "")

ADMIN_IDS = [int(x) for x in get_clean_var("ADMIN_IDS", "0").split()]
STORAGE_CHANNEL = int(get_clean_var("STORAGE_CHANNEL", "0")) 
SEARCH_CHAT = int(get_clean_var("SEARCH_CHAT", "0")) 
FSUB_CHANNEL = int(get_clean_var("FSUB_CHANNEL", "0")) 
MAIN_CHANNEL_LINK = get_clean_var("MAIN_CHANNEL_LINK", "https://t.me/Movies2026Cinema")

TMDB_API_KEY = get_clean_var("TMDB_API_KEY", "")
SHORT_DOMAIN = get_clean_var("SHORT_DOMAIN", "arolinks.com")
SHORT_API_KEY = get_clean_var("SHORT_API_KEY", "")

SHORTLINK_ENABLED = True 
user_cooldowns = {}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MovieBot(Client):
    def __init__(self):
        super().__init__("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
        self.movies = None

    async def start(self):
        await super().start()
        try:
            db_client = AsyncIOMotorClient(MONGO_URL)
            self.movies = db_client["PratapCinemaBot"]["movies"]
            print("✅ MongoDB Connected!")
        except Exception as e:
            print(f"❌ MongoDB Error: {e}")
        self.bot_info = await self.get_me()
        print(f"🚀 BOT @{self.bot_info.username} STARTED")

app = MovieBot()

# ================= HELPERS =================

async def get_shortlink(url):
    if not SHORTLINK_ENABLED: return url
    try:
        api_url = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=5) as resp:
                res = await resp.json()
                if res.get("status") == "success": return res["shortenedUrl"]
    except: pass
    return url

def clean_name(text):
    text = text.lower()
    junk = ['1080p', '720p', '480p', 'x264', 'x265', 'hevc', 'hindi', 'english', 'dual audio', 'web-dl', 'bluray']
    for word in junk: text = text.replace(word, '')
    text = re.sub(r'\(.*?\)|\[.*?\]', '', text)
    return " ".join(text.replace(".", " ").replace("_", " ").split()).strip()

async def delete_after_delay(msgs, delay):
    await asyncio.sleep(delay)
    for m in msgs:
        try: await m.delete()
        except: pass

# ================= SEARCH LOGIC =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "del"]))
async def search_movie(client, msg):
    # Fix: Handling Anonymous Admins and Missing Names
    user_id = msg.from_user.id if msg.from_user else msg.chat.id
    user_name = msg.from_user.first_name if msg.from_user else "Admin/User"
    
    current_time = time.time()
    if user_id in user_cooldowns:
        remaining = int(20 - (current_time - user_cooldowns[user_id]))
        if remaining > 0:
            m = await msg.reply(f"⏳ Wait `{remaining}s`...")
            return asyncio.create_task(delete_after_delay([m, msg], 5))

    user_cooldowns[user_id] = current_time
    query = clean_name(msg.text)
    if len(query) < 2: return

    sm = await client.send_message(msg.chat.id, f"🔍 Searching: `{msg.text}`...")

    try:
        keywords = query.split()
        regex_pattern = ".*".join(keywords)
        cursor = client.movies.find({"title": {"$regex": regex_pattern, "$options": "i"}})
        results = await cursor.to_list(length=20) 

        if not results:
            await sm.edit("❌ Not Found!")
            return asyncio.create_task(delete_after_delay([sm, msg], 10))

        buttons = []
        for item in results:
            bot_url = f"https://t.me/{client.bot_info.username}?start=file_{str(item['_id'])}"
            short_link = await get_shortlink(bot_url)
            buttons.append([InlineKeyboardButton(f"🎬 {item['title'][:40]}", url=short_link)])

        buttons.append([InlineKeyboardButton("✨ JOIN CHANNEL ✨", url=MAIN_CHANNEL_LINK)])
        
        cap = f"🎬 **Result for:** `{query.upper()}`\n👤 **User:** {user_name}"

        await client.send_message(msg.chat.id, cap, reply_markup=InlineKeyboardMarkup(buttons))
        await sm.delete()
        try: await msg.delete()
        except: pass
        
    except Exception as e:
        logger.error(f"Search Error: {e}")

# ================= START / FILE DELIVERY (FIXED) =================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    user_id = msg.from_user.id
    
    # FSUB Logic with Peer ID Safety
    if FSUB_CHANNEL:
        try:
            await client.get_chat_member(FSUB_CHANNEL, user_id)
        except (UserNotParticipant, PeerIdInvalid): # Error hone par bhi bot nahi rukega
            btn = [[InlineKeyboardButton("📢 JOIN CHANNEL 📢", url=MAIN_CHANNEL_LINK)]]
            if len(msg.command) > 1:
                btn.append([InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{client.bot_info.username}?start={msg.command[1]}")])
            return await msg.reply("❌ **Please Join Channel to get file!**", reply_markup=InlineKeyboardMarkup(btn))
        except Exception as e:
            logger.error(f"FSUB Crash Avoided: {e}")

    if len(msg.command) < 2:
        return await msg.reply("👋 Send movie name in group!")

    data = msg.command[1]
    if data.startswith("file_"):
        try:
            m_id = data.split("_")[1]
            res = await client.movies.find_one({"_id": ObjectId(m_id)})
            if res:
                sf = await client.send_cached_media(msg.chat.id, res["file_id"], 
                                                  caption=f"📂 **File:** `{res['title']}`\n\nDelete in 2 mins.")
                asyncio.create_task(delete_after_delay([sf], 120))
            else:
                await msg.reply("❌ File not found in DB.")
        except: pass

# ================= STORAGE =================

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    title = clean_name(msg.caption or file.file_name or "Unknown")
    await client.movies.insert_one({"title": title, "file_id": file.file_id})
    await msg.reply_text(f"✅ Added: `{title}`")

if __name__ == "__main__":
    app.run()    
    await msg.reply(
        f"🔍 **Results for:** `{msg.text}`\n👤 **User:** {msg.from_user.first_name if msg.from_user else 'Admin'}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# ================= START / FSUB / FILE DELIVERY =================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    user_id = msg.from_user.id
    
    if len(msg.command) > 1 and msg.command[1].startswith("file_"):
        # Force Join Check
        try:
            await client.get_chat_member(FSUB_CHANNEL, user_id)
        except:
            btn = [[InlineKeyboardButton("📢 JOIN CHANNEL 📢", url=MAIN_CHANNEL_LINK)]]
            return await msg.reply("❌ **Pehle channel join karein tabhi file milegi!**", reply_markup=InlineKeyboardMarkup(btn))

        # File Delivery
        m_id = msg.command[1].split("_")[1]
        db_client = AsyncIOMotorClient(MONGO_URL)
        res = await db_client["PratapCinemaBot"]["movies"].find_one({"_id": ObjectId(m_id)})
        
        if res:
            cap = f"📂 **File:** `{res['title']}`\n\n⚠️ Yeh file 2 minute mein delete ho jayegi!"
            sent_file = await client.send_cached_media(msg.chat.id, res["file_id"], caption=cap)
            
            # Log to Backup Channel
            await client.send_cached_media(LOG_CHANNEL, res["file_id"], caption=f"👤 User: {user_id}\n📂 File: {res['title']}")
            
            # Auto Delete
            asyncio.create_task(auto_delete(sent_file))
        else:
            await msg.reply("❌ File not found in Database.")
    else:
        await msg.reply("👋 Hello! Movie search karne ke liye hamare group ka use karein.")

# ================= STORAGE (Adding files to DB) =================

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    title = (msg.caption or file.file_name or "Unknown").lower()
    db_client = AsyncIOMotorClient(MONGO_URL)
    await db_client["PratapCinemaBot"]["movies"].insert_one({"title": title, "file_id": file.file_id})
    await msg.reply_text(f"✅ Added to DB: `{title}`")

# ================= ADMIN COMMANDS =================

@app.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats(client, msg):
    db_client = AsyncIOMotorClient(MONGO_URL)
    count = await db_client["PratapCinemaBot"]["movies"].count_documents({})
    await msg.reply(f"📊 **Total Movies in DB:** `{count}`")

if __name__ == "__main__":
    app.run()        try: await m.delete()
        except: pass

# ================= SEARCH LOGIC =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "del"]))
async def search_movie(client, msg):
    # Safe User Check (For Admins and Anonymous)
    user_id = msg.from_user.id if msg.from_user else msg.chat.id
    user_name = msg.from_user.first_name if msg.from_user else "Admin"
    
    current_time = time.time()
    if user_id in user_cooldowns:
        remaining = int(20 - (current_time - user_cooldowns[user_id]))
        if remaining > 0:
            m = await msg.reply(f"⏳ Please wait `{remaining}s`...")
            return asyncio.create_task(delete_after_delay([m, msg], 5))

    user_cooldowns[user_id] = current_time
    query = clean_name(msg.text)
    if len(query) < 2: return

    sm = await client.send_message(msg.chat.id, f"🔍 Searching for: `{msg.text}`...")

    try:
        # DB Search
        keywords = query.split()
        regex_pattern = ".*".join(keywords)
        cursor = client.movies.find({"title": {"$regex": regex_pattern, "$options": "i"}})
        results = await cursor.to_list(length=20) 

        if not results:
            await sm.edit(f"❌ `{msg.text}` not found!")
            return asyncio.create_task(delete_after_delay([sm, msg], 15))

        buttons = []
        for item in results:
            bot_url = f"https://t.me/{client.bot_info.username}?start=file_{str(item['_id'])}"
            short_link = await get_shortlink(bot_url)
            buttons.append([InlineKeyboardButton(f"🎬 {item['title'][:40]}", url=short_link)])

        buttons.append([InlineKeyboardButton("✨ JOIN CHANNEL ✨", url=MAIN_CHANNEL_LINK)])
        
        cap = f"🎬 **Results for:** `{query.upper()}`\n👤 **Requested by:** {user_name}"

        await client.send_message(msg.chat.id, cap, reply_markup=InlineKeyboardMarkup(buttons))
        
        # Cleanup
        await sm.delete()
        try: await msg.delete()
        except: pass # Bot might not have delete permission
        
    except Exception as e:
        logger.error(f"Search Error: {e}")

# ================= START / FILE DELIVERY =================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    user_id = msg.from_user.id
    
    # Robust FSUB Logic (Will NOT crash even if ID is wrong)
    if FSUB_CHANNEL:
        try:
            await client.get_chat_member(FSUB_CHANNEL, user_id)
        except (UserNotParticipant, PeerIdInvalid, Exception) as e:
            # Agar ID galat hai ya user join nahi hai
            btn = [[InlineKeyboardButton("📢 JOIN CHANNEL 📢", url=MAIN_CHANNEL_LINK)]]
            if len(msg.command) > 1:
                btn.append([InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{client.bot_info.username}?start={msg.command[1]}")])
            
            # Agar PeerIdInvalid hai, toh hum check skip kar sakte hain taaki user ko file mil jaye
            if isinstance(e, PeerIdInvalid):
                logger.error("FSUB ID IS WRONG! Skipping check for now.")
            else:
                return await msg.reply("❌ **Join channel first!**", reply_markup=InlineKeyboardMarkup(btn))

    if len(msg.command) < 2:
        return await msg.reply("👋 Hello! Search for movies in the group.")

    data = msg.command[1]
    if data.startswith("file_"):
        try:
            m_id = data.split("_")[1]
            res = await client.movies.find_one({"_id": ObjectId(m_id)})
            if res:
                sf = await client.send_cached_media(msg.chat.id, res["file_id"], 
                                                  caption=f"📂 **File:** `{res['title']}`\n\n⚠️ Will delete in 2 minutes.")
                asyncio.create_task(delete_after_delay([sf], 120))
            else:
                await msg.reply("❌ This file is no longer in our database.")
        except Exception as e:
            logger.error(f"File Delivery Error: {e}")

# ================= STORAGE =================

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    if not file: return
    title = clean_name(msg.caption or file.file_name or "Unknown")
    await client.movies.insert_one({"title": title, "file_id": file.file_id})
    await msg.reply_text(f"✅ Added to Database: `{title}`")

# ================= WEB SERVER =================
async def health_check(request):
    return web.Response(text="Bot is Alive")

async def start_web_server():
    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8080).start()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(start_web_server())
    app.run()
