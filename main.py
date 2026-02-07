import os
import re
import asyncio
import aiohttp
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant, MessageIdInvalid, FloodWait
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
LOG_CHANNEL = int(get_clean_var("LOG_CHANNEL", "0")) 
SEARCH_CHAT = int(get_clean_var("SEARCH_CHAT", "0")) 
FSUB_CHANNEL = int(get_clean_var("FSUB_CHANNEL", "0")) 
MAIN_CHANNEL_LINK = get_clean_var("MAIN_CHANNEL_LINK", "https://t.me/Movies2026Cinema")

TMDB_API_KEY = get_clean_var("TMDB_API_KEY", "")
SHORT_DOMAIN = get_clean_var("SHORT_DOMAIN", "arolinks.com")
SHORT_API_KEY = get_clean_var("SHORT_API_KEY", "")

SHORTLINK_ENABLED = True 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MovieBot(Client):
    def __init__(self):
        super().__init__("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
        self.mongo_client = None
        self.db = None
        self.movies = None
        self.db_error = None 

    async def start(self):
        await super().start()
        try:
            self.mongo_client = AsyncIOMotorClient(MONGO_URL)
            self.db = self.mongo_client["PratapCinemaBot"]
            self.movies = self.db["movies"]
            await self.db.command("ping")
            logger.info("✅ MongoDB Connected Successfully!")
        except Exception as e:
            self.db_error = str(e)
            logger.error(f"❌ MongoDB Error: {e}")
            
        self.bot_info = await self.get_me()
        logger.info(f"🚀 BOT @{self.bot_info.username} STARTED")

    async def stop(self, *args):
        await super().stop()
        if self.mongo_client:
            self.mongo_client.close()

app = MovieBot()

# ================== WEB SERVER ==================
async def health_check(request):
    return web.Response(text="Bot is Running")

async def start_web_server():
    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
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
    app.run()# ================= HELPERS =================

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
    # Season/Episode patterns ko preserve karne ke liye junk list update ki hai
    junk = ['1080p', '720p', '480p', 'x264', 'x265', 'hevc', 'hindi', 'english', 'dual audio', 'web-dl', 'bluray']
    for word in junk: text = text.replace(word, '')
    text = re.sub(r'\(.*?\)|\[.*?\]', '', text)
    return " ".join(text.replace(".", " ").replace("_", " ").split()).strip()

async def get_tmdb_info(query):
    # Year nikaal kar search karna behtar hota hai TMDB par
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
    original_query = msg.text
    query = clean_name(original_query)
    if len(query) < 2: return

    try: await msg.delete()
    except: pass

    sm = await client.send_message(msg.chat.id, f"🔍 **Searching for:** `{original_query}`...")

    if client.movies is None:
        return await sm.edit("⚠️ Database Connection Error.")

    try:
        # Step 1: Broad Search (Keywords match)
        keywords = query.split()
        regex_pattern = ".*".join(keywords)
        cursor = client.movies.find({"title": {"$regex": regex_pattern, "$options": "i"}})
        results = await cursor.to_list(length=15)

        # Step 2: Agar result nahi mila, toh year hata kar try karo
        if not results and any(char.isdigit() for char in query):
            clean_q = re.sub(r'\d{4}', '', query).strip()
            regex_pattern = ".*".join(clean_q.split())
            cursor = client.movies.find({"title": {"$regex": regex_pattern, "$options": "i"}})
            results = await cursor.to_list(length=15)

        if not results:
            await sm.edit(f"❌ `{original_query}` nahi mili! Spelling check karein.")
            asyncio.create_task(delete_after_delay([sm], 15))
            return 

        poster, m_title = await get_tmdb_info(query)
        
        buttons = []
        for item in results:
            bot_url = f"https://t.me/{client.bot_info.username}?start=file_{str(item['_id'])}"
            short_link = await get_shortlink(bot_url)
            # Yahan item['title'] dikhayega taaki user S01, S02 ya Episode pehchan sake
            btn_text = f"🎬 {item['title'][:45]}"
            buttons.append([InlineKeyboardButton(btn_text, url=short_link)])

        buttons.append([InlineKeyboardButton("✨ JOIN MAIN CHANNEL ✨", url=MAIN_CHANNEL_LINK)])
        
        cap = (f"🎬 **Movie Name:** `{m_title}`\n\n"
               f"👤 **User:** {msg.from_user.first_name if msg.from_user else 'User'}\n"
               f"🆔 **ID:** `{msg.from_user.id if msg.from_user else 'N/A'}`")

        if poster:
            res_msg = await client.send_photo(msg.chat.id, poster, caption=cap, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            res_msg = await client.send_message(msg.chat.id, cap, reply_markup=InlineKeyboardMarkup(buttons))
        
        await sm.delete()
        asyncio.create_task(delete_after_delay([res_msg], 300)) # 5 min delay
        
    except Exception as e:
        logger.error(f"Search Error: {e}")
        await sm.edit(f"⚠️ Ek error aaya hai, please admin ko batayein.")

# ================= START / FSUB =================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    user_id = msg.from_user.id
    
    # Force Join Fix
    if FSUB_CHANNEL:
        try:
            await client.get_chat_member(FSUB_CHANNEL, user_id)
        except UserNotParticipant:
            try:
                invite = (await client.get_chat(FSUB_CHANNEL)).invite_link
            except: invite = MAIN_CHANNEL_LINK
            
            btn = [[InlineKeyboardButton("📢 JOIN CHANNEL 📢", url=invite or MAIN_CHANNEL_LINK)]]
            if len(msg.command) > 1:
                btn.append([InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{client.bot_info.username}?start={msg.command[1]}")])
            return await msg.reply("❌ **Access Denied!**\n\nFile paane ke liye channel join karein.", reply_markup=InlineKeyboardMarkup(btn))
        except Exception: pass

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
        except:
            await msg.reply("❌ Invalid Link.")

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
            async with session.get(api_url, timeout=10) as resp:
                res = await resp.json()
                if res.get("status") == "success": return res["shortenedUrl"]
    except: pass
    return url

def clean_name(text):
    if not text: return ""
    text = text.lower()
    junk = [r'\(.*?\)', r'\[.*?\]', '1080p', '720p', '480p', 'x264', 'x265', 'hevc', 'hindi', 'english', 'dual audio', 'web-dl', 'bluray']
    for word in junk: text = re.sub(word, '', text)
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
                        poster = f"https://image.tmdb.org/t/p/w342{p_path}" if p_path else None # Small Poster
                        title = res.get('title') or res.get('name') or query.upper()
                        return poster, title
    except: pass
    return None, query.upper()

async def delete_after_delay(msgs, delay):
    await asyncio.sleep(delay)
    for m in msgs:
        try: await m.delete()
        except: pass

# ================= ADMIN COMMANDS (UNTOUCHED) =================

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
    title = clean_name(msg.caption or file.file_name or "Unknown")
    await client.movies.insert_one({"title": title, "file_id": file.file_id})
    await msg.reply_text(f"✅ Added: `{title}`")

# ================= SEARCH LOGIC (FIXED) =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "shortlink", "del"]))
async def search_movie(client, msg):
    original_query = msg.text
    query = clean_name(original_query)
    if len(query) < 3: return

    try: await msg.delete()
    except: pass

    sm = await client.send_message(msg.chat.id, f"🔍 **Searching for:** `{original_query}`...")

    # Fuzzy logic: Har word ko alag karke regex banaya taaki spelling mistake cover ho sake
    keywords = query.split()
    regex_pattern = ".*".join(keywords)
    
    cursor = client.movies.find({"title": {"$regex": regex_pattern, "$options": "i"}})
    results = await cursor.to_list(length=15) # Multiple results handle karne ke liye

    if not results:
        await sm.edit(f"❌ `{original_query}` nahi mili! Spelling check karein.")
        asyncio.create_task(delete_after_delay([sm], 15))
        return 

    poster, m_title = await get_tmdb_info(query)
    
    buttons = []
    # Loop for multiple episodes/files
    for item in results:
        bot_url = f"https://t.me/{client.bot_info.username}?start=file_{str(item['_id'])}"
        short_link = await get_shortlink(bot_url)
        # Button text mein file ka naam aayega (Episodes/Part handle karne ke liye)
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
        asyncio.create_task(delete_after_delay([res_msg], 180))
    except Exception as e:
        logger.error(f"Search Send Error: {e}")

# ================= START / FSUB (FIXED) =================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    user_id = msg.from_user.id
    
    # Force Join Logic
    if FSUB_CHANNEL:
        try:
            await client.get_chat_member(FSUB_CHANNEL, user_id)
        except UserNotParticipant:
            invite = (await client.get_chat(FSUB_CHANNEL)).invite_link or MAIN_CHANNEL_LINK
            btn = [[InlineKeyboardButton("📢 JOIN CHANNEL 📢", url=invite)]]
            if len(msg.command) > 1:
                btn.append([InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{client.bot_info.username}?start={msg.command[1]}")])
            return await msg.reply("❌ **Access Denied!**\n\nFile paane ke liye channel join karein.", reply_markup=InlineKeyboardMarkup(btn))
        except Exception: pass

    if len(msg.command) < 2:
        return await msg.reply("👋 Namaste! Group mein movie search karein.")

    data = msg.command[1]
    if data.startswith("file_"):
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

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(start_web_server())
    app.run()
