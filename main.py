import os
import re
import asyncio
import aiohttp
import logging
import difflib 
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
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

# Admin & Channels
ADMIN_IDS = [int(x) for x in get_clean_var("ADMIN_IDS", "0").split()]
STORAGE_CHANNEL = int(get_clean_var("STORAGE_CHANNEL", "0")) 
LOG_CHANNEL = int(get_clean_var("LOG_CHANNEL", "0")) 
SEARCH_CHAT = int(get_clean_var("SEARCH_CHAT", "0")) 
FSUB_CHANNEL = int(get_clean_var("FSUB_CHANNEL", "0")) 
MAIN_CHANNEL_LINK = get_clean_var("MAIN_CHANNEL_LINK", "https://t.me/Movies2026Cinema")

# APIs
TMDB_API_KEY = get_clean_var("TMDB_API_KEY", "")
SHORT_DOMAIN = get_clean_var("SHORT_DOMAIN", "arolinks.com")
SHORT_API_KEY = get_clean_var("SHORT_API_KEY", "")

# Settings
SHORTLINK_ENABLED = True 

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MovieBot(Client):
    def __init__(self):
        super().__init__("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
        self.mongo_client = None
        self.db = None
        self.movies = None

    async def start(self):
        await super().start()
        try:
            self.mongo_client = AsyncIOMotorClient(MONGO_URL)
            self.db = self.mongo_client["PratapCinemaBot"]
            self.movies = self.db["movies"]
            await self.db.command("ping")
            print("✅ MongoDB Connected Successfully!")
        except Exception as e:
            print(f"❌ MongoDB Error: {e}")
            self.movies = None 
            
        self.bot_info = await self.get_me()
        print(f"🚀 BOT @{self.bot_info.username} STARTED")

    async def stop(self, *args):
        await super().stop()
        if self.mongo_client:
            self.mongo_client.close()

app = MovieBot()

# ================= HELPERS (SMART CLEANING) =================

async def get_shortlink(url):
    global SHORTLINK_ENABLED
    if not SHORTLINK_ENABLED: 
        return url
    try:
        api_url = f"https://{SHORT_DOMAIN}/api?api={SHORT_API_KEY}&url={url}"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=10) as resp:
                res = await resp.json()
                if res.get("status") == "success": 
                    return res["shortenedUrl"]
    except Exception as e:
        logger.error(f"Shortlink Error: {e}")
    return url

def clean_name(text):
    if not text: return ""
    text = text.lower()
    junk = [r'\b\d{4}\b', r'1080p', r'720p', r'480p', r'x264', r'x265', r'hevc', r'hindi', r'english', 
            r'dual audio', r'web-dl', r'bluray', r'camrip', r'pre-dvd', r'cinevood', r'\.mkv', r'\.mp4', r'\.avi']
    for word in junk: 
        text = re.sub(word, '', text)
    text = re.sub(r'[^\w\s]', '', text)
    return " ".join(text.split()).strip()

async def get_tmdb_info(query):
    clean_q = clean_name(query)
    search_q = " ".join(clean_q.split()[:4]) 
    
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={search_q}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('results'):
                        res = data['results'][0]
                        p_path = res.get('backdrop_path') or res.get('poster_path')
                        poster = f"https://image.tmdb.org/t/p/w780{p_path}" if p_path else None
                        title = res.get('title') or res.get('name') or query
                        rating = res.get('vote_average', 'N/A')
                        year = (res.get('release_date') or res.get('first_air_date') or "0000")[:4]
                        return poster, title, rating, year
    except: pass
    return None, query, "N/A", "0000"

async def delete_after_delay(msgs, delay):
    await asyncio.sleep(delay)
    for m in msgs:
        try: await m.delete()
        except: pass

# ================= ADMIN COMMANDS =================

@app.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats_cmd(client, msg):
    if client.movies is None:
        return await msg.reply(f"⚠️ **Database Error!**")
    try:
        count = await client.movies.count_documents({})
        await msg.reply(f"📊 **Total Movies:** `{count}`")
    except: pass

@app.on_message(filters.command("shortlink") & filters.user(ADMIN_IDS))
async def toggle_shortlink_cmd(client, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2: return await msg.reply("/shortlink on/off")
    SHORTLINK_ENABLED = (msg.command[1].lower() == "on")
    await msg.reply(f"✅ Shortlink: {SHORTLINK_ENABLED}")

@app.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie_cmd(client, msg):
    if client.movies is None: return
    query = " ".join(msg.command[1:])
    try:
        result = await client.movies.delete_many({"title": {"$regex": query, "$options": "i"}})
        await msg.reply(f"🗑️ `{result.deleted_count}` movies deleted.")
    except Exception as e: await msg.reply(f"Error: {e}")

# ================= STORAGE INDEXING =================

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    if client.movies is None: return
    file = msg.video or msg.document
    if not file: return
    
    title = msg.caption or file.file_name or "Unknown"
    
    movie_data = {"title": title, "file_id": file.file_id, "caption": title}
    try:
        await client.movies.insert_one(movie_data)
        await msg.reply_text(f"✅ **Added:** `{title}`")
    except: pass

# ================= SEARCH LOGIC =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "shortlink", "del"]))
async def search_movie(client, msg):
    raw_query = msg.text
    query = clean_name(raw_query) 
    
    if len(query) < 2: return

    try: await msg.delete()
    except: pass

    u_mention = msg.from_user.mention
    u_id = msg.from_user.id

    sm = await client.send_message(msg.chat.id, f"🔍 **Searching:** `{raw_query}` \n⏳ Please wait...")

    if client.movies is None:
        await sm.edit("❌ DB Error.")
        return

    res = None
    try:
        res = await client.movies.find_one({"title": {"$regex": query, "$options": "i"}})
    except: pass

    if not res:
        all_movies_cursor = client.movies.find({}, {"title": 1})
        title_map = {}
        cleaned_titles = []
        
        async for doc in all_movies_cursor:
            original = doc.get('title')
            if original:
                c_title = clean_name(original)
                cleaned_titles.append(c_title)
                title_map[c_title] = doc['_id']
        
        matches = difflib.get_close_matches(query, cleaned_titles, n=1, cutoff=0.4)
        if matches:
            found_clean_name = matches[0]
            res = await client.movies.find_one({"_id": title_map[found_clean_name]})
    
    if res:
        db_id = str(res["_id"]) 
        search_term_for_tmdb = clean_name(res["title"])
        if len(search_term_for_tmdb) < 2: search_term_for_tmdb = query

        poster, m_title, m_rating, m_year = await get_tmdb_info(search_term_for_tmdb)

        bot_url = f"https://t.me/{client.bot_info.username}?start=file_{db_id}"
        final_link = await get_shortlink(bot_url)

        btn = InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 GET MOVIE FILE 📂", url=final_link)],
            [InlineKeyboardButton("🎥 How to Download", url=MAIN_CHANNEL_LINK)]
        ])

        cap = (f"🎬 **Title:** `{m_title}`\n"
               f"⭐ **Rating:** `{m_rating}`  |  📅 **Year:** `{m_year}`\n\n"
               f"👤 **User:** {u_mention}\n"
               f"🆔 **ID:** `{u_id}`\n\n"
               f"🔍 **Search by:** `{raw_query}`")

        if poster:
            res_msg = await client.send_photo(msg.chat.id, poster, caption=cap, reply_markup=btn)
        else:
            res_msg = await client.send_message(msg.chat.id, cap, reply_markup=btn, disable_web_page_preview=True)
            
        await sm.delete()
        asyncio.create_task(delete_after_delay([res_msg], 180))

    else:
        req_text = (f"❌ **Movie Nahi Mili!**\n\n"
                    f"👤 {u_mention}, humare paas `{raw_query}` nahi hai.\n"
                    f"📩 **Admin ko request bhej di gayi hai.**\n"
                    f"✅ Jaldi add kar di jayegi.")
        
        await sm.edit(req_text)
        
        if LOG_CHANNEL:
            try:
                await client.send_message(LOG_CHANNEL, 
                    f"⚠️ **REQUEST:** `{raw_query}`\n👤 By: {u_mention}\n🆔 `{u_id}`")
            except: pass
        
        asyncio.create_task(delete_after_delay([sm], 20))

# ================= START / FILE SENDING =================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    user_id = msg.from_user.id
    try:
        await client.get_chat_member(FSUB_CHANNEL, user_id)
    except UserNotParticipant:
        try:
            invite = (await client.get_chat(FSUB_CHANNEL)).invite_link
        except: invite = MAIN_CHANNEL_LINK 
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 JOIN CHANNEL FIRST", url=invite)],
                                    [InlineKeyboardButton("✅ TRY AGAIN", url=f"https://t.me/{client.bot_info.username}?start={msg.command[1] if len(msg.command)>1 else ''}")]])
        return await msg.reply("❌ **Pehle Channel Join Karein!**", reply_markup=btn)
    except: pass

    if len(msg.command) < 2:
        return await msg.reply("👋 Group me movie search karein.")

    data = msg.command[1]
    if data.startswith("file_"):
        if client.movies is None: return await msg.reply("❌ DB Error")
        try:
            m_id = data.split("_")[1]
            res = await client.movies.find_one({"_id": ObjectId(m_id)})
        except: return await msg.reply("❌ Link Expired.")
        
        if res:
            f_id = res["file_id"]
            title = res["title"]
            
            caption = (f"🎬 **{title}**\n\n"
                       f"❤️ {MAIN_CHANNEL_LINK} ❤️\n"
                       f"👤 **Admin:** Pratap 🇮🇳\n\n"
                       f"⚠️ 2 min me delete ho jayegi!")
            
            sf = await client.send_cached_media(msg.chat.id, f_id, caption=caption)
            asyncio.create_task(delete_after_delay([sf], 120))
        else:
            await msg.reply("❌ File nahi mili.")

# ================= SERVER & BOT RUN =================

async def health_check(request):
    return web.Response(text="Bot is Alive & Running")

async def start_web_server():
    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(start_web_server())
    app.run()
