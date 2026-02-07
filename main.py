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
            print("✅ MongoDB Connected Successfully!")
        except Exception as e:
            print(f"❌ MongoDB Connection Error: {e}")
        self.bot_info = await self.get_me()
        print(f"🚀 BOT @{self.bot_info.username} IS ONLINE")

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
    if not text: return ""
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
    app.run()        try: await m.delete()
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
    app.run()    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

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
    if not text: return ""
    text = text.lower()
    junk = ['1080p', '720p', '480p', 'x264', 'x265', 'hevc', 'hindi', 'english', 'dual audio', 'web-dl', 'bluray']
    for word in junk: text = text.replace(word, '')
    text = re.sub(r'\(.*?\)|\[.*?\]', '', text)
    return " ".join(text.replace(".", " ").replace("_", " ").split()).strip()

async def get_tmdb_info(query):
    search_q = re.sub(r'\d{4}', '', query).strip()
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={search_q}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('results'):
                        res = data['results'][0]
                        p_path = res.get('poster_path') or res.get('backdrop_path')
                        poster = f"https://image.tmdb.org/t/p/w342{p_path}" if p_path else None
                        title = res.get('title') or res.get('name') or query.upper()
                        return poster, title
    except: pass
    return None, query.upper()

async def delete_after_delay(msgs, delay):
    await asyncio.sleep(delay)
    for m in msgs:
        try: await m.delete()
        except: pass

# ================= ADMIN COMMANDS =================

@app.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats_cmd(client, msg):
    count = await client.movies.count_documents({})
    await msg.reply(f"📊 **Total Movies:** `{count}`")

@app.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie_cmd(client, msg):
    if len(msg.command) < 2: return
    query = " ".join(msg.command[1:])
    result = await client.movies.delete_many({"title": {"$regex": query, "$options": "i"}})
    await msg.reply(f"🗑️ Deleted `{result.deleted_count}` items.")

# ================= STORAGE =================

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    if not file: return
    title = clean_name(msg.caption or file.file_name or "Unknown")
    await client.movies.insert_one({"title": title, "file_id": file.file_id})
    await msg.reply_text(f"✅ Added: `{title}`")

# ================= SEARCH LOGIC (WITH COOLDOWN) =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "del"]))
async def search_movie(client, msg):
    if not msg.from_user: return
    
    user_id = msg.from_user.id
    current_time = time.time()
    
    # 20 Second Anti-Spam Check
    if user_id in user_cooldowns:
        remaining = int(20 - (current_time - user_cooldowns[user_id]))
        if remaining > 0:
            m = await msg.reply(f"⏳ **Anti-Spam:** Please wait `{remaining}s` before next search.")
            asyncio.create_task(delete_after_delay([m, msg], 5))
            return

    user_cooldowns[user_id] = current_time
    original_query = msg.text
    query = clean_name(original_query)
    if len(query) < 2: return

    sm = await client.send_message(msg.chat.id, f"🔍 **Searching for:** `{original_query}`...")

    try:
        # Step 1: Search Database (Limit 20 results for Series/Multiple files)
        keywords = query.split()
        regex_pattern = ".*".join(keywords)
        cursor = client.movies.find({"title": {"$regex": regex_pattern, "$options": "i"}})
        results = await cursor.to_list(length=20) 

        if not results:
            await sm.edit(f"❌ `{original_query}` nahi mili!")
            asyncio.create_task(delete_after_delay([sm, msg], 10))
            return 

        poster, m_title = await get_tmdb_info(query)
        
        buttons = []
        for item in results:
            bot_url = f"https://t.me/{client.bot_info.username}?start=file_{str(item['_id'])}"
            short_link = await get_shortlink(bot_url)
            buttons.append([InlineKeyboardButton(f"🎬 {item['title'][:45]}", url=short_link)])

        buttons.append([InlineKeyboardButton("✨ JOIN CHANNEL ✨", url=MAIN_CHANNEL_LINK)])
        
        cap = (f"🎬 **Movie Name:** `{m_title}`\n\n"
               f"👤 **User:** {msg.from_user.first_name}\n"
               f"🆔 **ID:** `{user_id}`")

        if poster:
            res_msg = await client.send_photo(msg.chat.id, poster, caption=cap, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            res_msg = await client.send_message(msg.chat.id, cap, reply_markup=InlineKeyboardMarkup(buttons))
        
        await sm.delete()
        try: await msg.delete()
        except: pass
        asyncio.create_task(delete_after_delay([res_msg], 300))
        
    except Exception as e:
        logger.error(f"Search Error: {e}")

# ================= START / FSUB =================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    if not msg.from_user: return
    user_id = msg.from_user.id
    
    if FSUB_CHANNEL:
        try:
            await client.get_chat_member(FSUB_CHANNEL, user_id)
        except UserNotParticipant:
            try:
                chat = await client.get_chat(FSUB_CHANNEL)
                invite = chat.invite_link or MAIN_CHANNEL_LINK
            except: invite = MAIN_CHANNEL_LINK
            
            btn = [[InlineKeyboardButton("📢 JOIN CHANNEL 📢", url=invite)]]
            if len(msg.command) > 1:
                btn.append([InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{client.bot_info.username}?start={msg.command[1]}")])
            return await msg.reply("❌ **Access Denied!**\n\nFile paane ke liye channel join karein.", reply_markup=InlineKeyboardMarkup(btn))
        except: pass

    if len(msg.command) < 2:
        return await msg.reply("👋 Namaste! Group mein movie search karein.")

    data = msg.command[1]
    if data.startswith("file_"):
        try:
            m_id = data.split("_")[1]
            res = await client.movies.find_one({"_id": ObjectId(m_id)})
            if res:
                sf = await client.send_cached_media(msg.chat.id, res["file_id"], 
                                                  caption=f"📂 **File Name:** `{res['title']}`\n\n⚠️ Delete in 2 mins.")
                asyncio.create_task(delete_after_delay([sf], 120))
            else:
                await msg.reply("❌ File Not Found.")
        except: pass

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(start_web_server())
    app.run()    await web.TCPSite(runner, "0.0.0.0", port).start()

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
    if not text: return ""
    text = text.lower()
    junk = ['1080p', '720p', '480p', 'x264', 'x265', 'hevc', 'hindi', 'english', 'dual audio', 'web-dl', 'bluray', '2015', '2016', '2024', '2025']
    for word in junk: text = text.replace(word, '')
    text = re.sub(r'\(.*?\)|\[.*?\]', '', text)
    return " ".join(text.replace(".", " ").replace("_", " ").split()).strip()

async def get_tmdb_info(query):
    search_q = re.sub(r'\d{4}', '', query).strip()
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={search_q}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('results'):
                        res = data['results'][0]
                        p_path = res.get('poster_path') or res.get('backdrop_path')
                        poster = f"https://image.tmdb.org/t/p/w342{p_path}" if p_path else None
                        title = res.get('title') or res.get('name') or query.upper()
                        return poster, title
    except: pass
    return None, query.upper()

async def delete_after_delay(msgs, delay):
    await asyncio.sleep(delay)
    for m in msgs:
        try: await m.delete()
        except: pass

# ================= ADMIN COMMANDS =================

@app.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats_cmd(client, msg):
    if client.movies is None: return await msg.reply("⚠️ DB Error")
    count = await client.movies.count_documents({})
    await msg.reply(f"📊 **Total Movies:** `{count}`")

@app.on_message(filters.command("shortlink") & filters.user(ADMIN_IDS))
async def toggle_shortlink_cmd(client, msg):
    global SHORTLINK_ENABLED
    SHORTLINK_ENABLED = not SHORTLINK_ENABLED
    await msg.reply(f"✅ Shortlink: {'Enabled' if SHORTLINK_ENABLED else 'Disabled'}")

@app.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie_cmd(client, msg):
    if len(msg.command) < 2: return
    query = " ".join(msg.command[1:])
    result = await client.movies.delete_many({"title": {"$regex": query, "$options": "i"}})
    await msg.reply(f"🗑️ Deleted `{result.deleted_count}` items.")

# ================= STORAGE =================

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    file = msg.video or msg.document
    if not file: return
    title = clean_name(msg.caption or file.file_name or "Unknown")
    await client.movies.insert_one({"title": title, "file_id": file.file_id})
    await msg.reply_text(f"✅ Added: `{title}`")

# ================= SEARCH LOGIC =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "shortlink", "del"]))
async def search_movie(client, msg):
    if not msg.from_user: return # Fixed: Attribute 'first_name' error
    
    original_query = msg.text
    query = clean_name(original_query)
    if len(query) < 2: return

    try: await msg.delete()
    except: pass

    sm = await client.send_message(msg.chat.id, f"🔍 **Searching for:** `{original_query}`...")

    if client.movies is None:
        return await sm.edit("⚠️ Database Connection Error.")

    try:
        # Search Optimization
        keywords = query.split()
        regex_pattern = ".*".join(keywords)
        cursor = client.movies.find({"title": {"$regex": regex_pattern, "$options": "i"}})
        results = await cursor.to_list(length=15)

        if not results:
            try:
                await sm.edit(f"❌ `{original_query}` nahi mili! Spelling check karein.")
                asyncio.create_task(delete_after_delay([sm], 15))
            except MessageIdInvalid: pass
            return 

        poster, m_title = await get_tmdb_info(query)
        
        buttons = []
        for item in results:
            bot_url = f"https://t.me/{client.bot_info.username}?start=file_{str(item['_id'])}"
            short_link = await get_shortlink(bot_url)
            buttons.append([InlineKeyboardButton(f"🎬 {item['title'][:40]}...", url=short_link)])

        buttons.append([InlineKeyboardButton("✨ JOIN CHANNEL ✨", url=MAIN_CHANNEL_LINK)])
        
        cap = (f"🎬 **Movie Name:** `{m_title}`\n\n"
               f"👤 **User:** {msg.from_user.first_name}\n"
               f"🆔 **ID:** `{msg.from_user.id}`")

        try:
            if poster:
                res_msg = await client.send_photo(msg.chat.id, poster, caption=cap, reply_markup=InlineKeyboardMarkup(buttons))
            else:
                res_msg = await client.send_message(msg.chat.id, cap, reply_markup=InlineKeyboardMarkup(buttons))
            
            await sm.delete()
            asyncio.create_task(delete_after_delay([res_msg], 300))
        except MessageIdInvalid: pass
        except FloodWait as e:
            await asyncio.sleep(e.value)
        
    except Exception as e:
        logger.error(f"Search Error: {e}")

# ================= START / FSUB =================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    if not msg.from_user: return
    user_id = msg.from_user.id
    
    # Force Join Logic
    if FSUB_CHANNEL:
        try:
            await client.get_chat_member(FSUB_CHANNEL, user_id)
        except UserNotParticipant:
            try:
                invite = (await client.get_chat(FSUB_CHANNEL)).invite_link or MAIN_CHANNEL_LINK
            except: invite = MAIN_CHANNEL_LINK
            
            btn = [[InlineKeyboardButton("📢 JOIN CHANNEL 📢", url=invite)]]
            if len(msg.command) > 1:
                btn.append([InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{client.bot_info.username}?start={msg.command[1]}")])
            return await msg.reply("❌ **Access Denied!**\n\nFile paane ke liye channel join karein.", reply_markup=InlineKeyboardMarkup(btn))
        except Exception as e:
            logger.error(f"FSUB Error: {e}")

    if len(msg.command) < 2:
        return await msg.reply("👋 Namaste! Group mein movie search karein.")

    data = msg.command[1]
    if data.startswith("file_"):
        try:
            m_id = data.split("_")[1]
            res = await client.movies.find_one({"_id": ObjectId(m_id)})
            if res:
                caption = (f"📂 **File Name:** `{res['title']}`\n"
                           f"👤 **Admin:** pratap 🇮🇳❤️\n\n"
                           f"⚠️ **Note:** Yeh file 2 minute mein delete ho jayegi!")
                sf = await client.send_cached_media(msg.chat.id, res["file_id"], caption=caption)
                asyncio.create_task(delete_after_delay([sf], 120))
            else:
                await msg.reply("❌ File Not Found.")
        except: pass

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(start_web_server())
    app.run()
