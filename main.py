import os
import re
import asyncio
import aiohttp
import logging
import difflib  # Spelling check ke liye add kiya hai
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId
from aiohttp import web

# ================= CONFIGURATION (Fixed) =================
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

# Logging
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
            print("✅ MongoDB Connected Successfully!")
            self.db_error = None
        except Exception as e:
            print(f"❌ MongoDB Error: {e}")
            self.db_error = str(e)
            self.movies = None 
            
        self.bot_info = await self.get_me()
        print(f"🚀 BOT @{self.bot_info.username} STARTED")

    async def stop(self, *args):
        await super().stop()
        if self.mongo_client:
            self.mongo_client.close()

app = MovieBot()

# ================== WEB SERVER ==================
async def health_check(request):
    return web.Response(text="Bot is Alive & Running")

async def start_web_server():
    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

# ================= HELPERS =================

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
                        p_path = res.get('backdrop_path') or res.get('poster_path')
                        poster = f"https://image.tmdb.org/t/p/w780{p_path}" if p_path else None
                        title = res.get('title') or res.get('name') or query.upper()
                        rating = res.get('vote_average', 'N/A')
                        year = (res.get('release_date') or res.get('first_air_date') or "0000")[:4]
                        return poster, title, rating, year
    except: pass
    return None, query.upper(), "N/A", "0000"

async def delete_after_delay(msgs, delay):
    await asyncio.sleep(delay)
    for m in msgs:
        try: await m.delete()
        except: pass

# ================= ADMIN COMMANDS =================

@app.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats_cmd(client, msg):
    if client.movies is None:
        err_msg = client.db_error if client.db_error else "Unknown Connection Error"
        return await msg.reply(f"⚠️ **Database Connected Nahi Hai!**\n\n🔍 **Error Reason:** `{err_msg}`")
    
    try:
        count = await client.movies.count_documents({})
        await msg.reply(f"📊 **Total Movies in MongoDB:** `{count}`")
    except Exception as e:
        await msg.reply(f"Error fetching stats: {e}")

@app.on_message(filters.command("shortlink") & filters.user(ADMIN_IDS))
async def toggle_shortlink_cmd(client, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2:
        return await msg.reply("Usage: `/shortlink on` or `/shortlink off`")
    
    choice = msg.command[1].lower()
    if choice == "on":
        SHORTLINK_ENABLED = True
        await msg.reply("✅ Shortlink has been **ENABLED**.")
    elif choice == "off":
        SHORTLINK_ENABLED = False
        await msg.reply("❌ Shortlink has been **DISABLED**.")

@app.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie_cmd(client, msg):
    if client.movies is None:
        return await msg.reply("⚠️ Database Connect Nahi Hai.")

    if len(msg.command) < 2:
        return await msg.reply("Usage: `/del movie_name`")
    query = " ".join(msg.command[1:])
    try:
        result = await client.movies.delete_many({"title": {"$regex": query, "$options": "i"}})
        await msg.reply(f"🗑️ `{result.deleted_count}` movies removed matching `{query}`.")
    except Exception as e:
        await msg.reply(f"Error: {e}")

# ================= STORAGE INDEXING & NOTIFICATION =================

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    if client.movies is None:
        return 
        
    file = msg.video or msg.document
    if not file: return

    title = clean_name(msg.caption or file.file_name or "Unknown")
    
    movie_data = {
        "title": title,
        "file_id": file.file_id,
        "caption": msg.caption or title
    }
    
    try:
        await client.movies.insert_one(movie_data)
        await msg.reply_text(f"✅ **Movie Added:** `{title}`")
        
        if LOG_CHANNEL:
            await client.send_message(
                LOG_CHANNEL,
                f"🎬 **New Movie Added!**\n\n"
                f"📛 **Name:** `{title}`\n"
                f"✅ **Status:** Uploaded to Database\n"
                f"🤖 **Bot:** @{client.bot_info.username}"
            )
    except Exception as e:
        print(f"DB Insert Error: {e}")

# ================= SEARCH LOGIC (MODIFIED) =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "shortlink", "del"]))
async def search_movie(client, msg):
    raw_query = msg.text
    query = clean_name(raw_query)
    if len(query) < 2: return

    try: await msg.delete()
    except: pass

    u_name = msg.from_user.first_name if msg.from_user else "User"
    u_mention = msg.from_user.mention if msg.from_user else "User"

    sm = await client.send_message(msg.chat.id, f"🔍 **Searching:** `{raw_query}`...")

    if client.movies is None:
        await sm.edit("❌ **Error:** Database Connected Nahi Hai. Admin ko batayein.")
        return

    # 1. Pehle Direct/Regex Search Try karein
    try:
        res = await client.movies.find_one({"title": {"$regex": query, "$options": "i"}})
    except Exception as e:
        await sm.edit("❌ Database Error.")
        return

    # 2. Agar movie nahi mili, to Fuzzy (Spelling Mistake) Check karein
    if not res:
        # DB se saare titles fetch karein (sirf titles, performance ke liye)
        all_movies_cursor = client.movies.find({}, {"title": 1})
        all_titles = []
        title_map = {}
        async for doc in all_movies_cursor:
            t = doc.get('title')
            if t:
                all_titles.append(t)
                title_map[t] = doc['_id']
        
        # Closest match dhoondo (standard python library use karke)
        matches = difflib.get_close_matches(query, all_titles, n=1, cutoff=0.5)
        
        if matches:
            # Agar spelling mistake wali movie mil gayi
            better_title = matches[0]
            res = await client.movies.find_one({"_id": title_map[better_title]})
            
            # User ko bata bhi do ki correct naam ye hai
            await sm.edit(f"💡 **Did you mean:** `{better_title}`?\n🔍 Found it!")
            await asyncio.sleep(1) # Thoda wait tak user padh le
        else:
            # 3. Agar ab bhi nahi mili -> Request bhejo Admin ko
            request_text = (
                f"❌ **Movie Nahi Mili!**\n\n"
                f"👋 Hello {u_mention},\n"
                f"Aapki movie `{raw_query}` hamare database mein nahi hai.\n\n"
                f"📩 **Humne Admin ko SMS kar diya hai.**\n"
                f"✅ **Aapki movie jaldi add kar di jayegi.**"
            )
            await sm.edit(request_text)
            
            # Admin / Log Channel par notification
            if LOG_CHANNEL:
                admin_alert = (
                    f"⚠️ **New Movie Request!** ⚠️\n\n"
                    f"👤 **User:** {u_mention} (`{msg.from_user.id}`)\n"
                    f"🎬 **Searched For:** `{raw_query}`\n"
                    f"📉 **Bot:** @{client.bot_info.username}\n\n"
                    f"📢 **Action:** Please is movie ko Database me add karein!"
                )
                try:
                    await client.send_message(LOG_CHANNEL, admin_alert)
                except Exception as e:
                    print(f"Admin Notify Error: {e}")
            
            asyncio.create_task(delete_after_delay([sm], 20))
            return 

    # Agar Movie Mil Gayi (Direct ya Spelling fix ke baad) -> Process karein
    db_id = str(res["_id"]) 
    db_title = res["title"]
    
    poster, m_title, m_rating, m_year = await get_tmdb_info(db_title)

    bot_url = f"https://t.me/{client.bot_info.username}?start=file_{db_id}"
    final_link = await get_shortlink(bot_url)

    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 DOWNLOAD / WATCH NOW", url=final_link)],
                                [InlineKeyboardButton("✨ JOIN CHANNEL ✨", url=MAIN_CHANNEL_LINK)]])

    cap = (f"✅ **Movie Mil Gayi!**\n\n🎬 **Naam:** `{db_title}`\n"
           f"🌟 **Rating:** `{m_rating}` | 📅 **Year:** `{m_year}`\n\n"
           f"👤 **User:** {u_name}\n\n"
           f"👁️ 2  [Movies 2026 - Cinema Pratap\"❤️🌹]({MAIN_CHANNEL_LINK})")

    if poster:
        res_msg = await client.send_photo(msg.chat.id, poster, caption=cap, reply_markup=btn)
    else:
        res_msg = await client.send_message(msg.chat.id, cap, reply_markup=btn)
    
    await sm.delete()
    asyncio.create_task(delete_after_delay([res_msg], 120))

# ================= START / FSUB =================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    user_id = msg.from_user.id
    
    try:
        await client.get_chat_member(FSUB_CHANNEL, user_id)
    except UserNotParticipant:
        try:
            invite = (await client.get_chat(FSUB_CHANNEL)).invite_link
        except:
            invite = MAIN_CHANNEL_LINK 
            
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 JOIN CHANNEL FIRST 📢", url=invite)],
                                    [InlineKeyboardButton("✅ TRY AGAIN", url=f"https://t.me/{client.bot_info.username}?start={msg.command[1] if len(msg.command)>1 else ''}")]])
        return await msg.reply("❌ **Access Denied!**\n\nFile paane ke liye pehle niche diye gaye channel ko join karein.", reply_markup=btn)
    except Exception as e:
        print(f"Join Check Error: {e}")

    if len(msg.command) < 2:
        return await msg.reply("👋 Namaste! Group me movie search karein.")

    data = msg.command[1]
    if data.startswith("file_"):
        if client.movies is None:
            return await msg.reply("❌ Database disconnected.")
            
        m_id = data.split("_")[1]
        
        try:
            res = await client.movies.find_one({"_id": ObjectId(m_id)})
        except:
            return await msg.reply("❌ Invalid Link or File Removed.")
        
        if res:
            f_id = res["file_id"]
            title = res["title"]
            caption = (f"📂 **File Name:** `{title}`\n👤 **Admin:** pratap 🇮🇳❤️\n\n"
                       f"🚀 **Channel:** {MAIN_CHANNEL_LINK}\n\n"
                       f"👁️ 2  [Movies 2026 - Cinema Pratap\"❤️🌹]({MAIN_CHANNEL_LINK})\n\n"
                       f"⚠️ **Warning:** 2 minute me delete ho jayegi!")
            
            sf = await client.send_cached_media(msg.chat.id, f_id, caption=caption)
            asyncio.create_task(delete_after_delay([sf], 120))
        else:
            await msg.reply("❌ File Not Found in Database.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(start_web_server())
    app.run()# ================== WEB SERVER ==================
async def health_check(request):
    return web.Response(text="Bot is Alive & Running")

async def start_web_server():
    server = web.Application()
    server.router.add_get("/", health_check)
    runner = web.AppRunner(server)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

# ================= HELPERS =================

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
    # Junk words removal
    junk = [r'\(.*?\)', r'\[.*?\]', '1080p', '720p', '480p', 'x264', 'x265', 'hevc', 'hindi', 'english', 'dual audio', 'web-dl', 'bluray', 'camrip', 'pre-dvd']
    for word in junk: text = re.sub(word, '', text)
    # Special characters ko space se replace karo taaki 'Border2' -> 'Border 2' jaisa ban sake
    text = re.sub(r'[^a-zA-Z0-9]', ' ', text)
    return " ".join(text.split()).strip()

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
                        p_path = res.get('backdrop_path') or res.get('poster_path')
                        poster = f"https://image.tmdb.org/t/p/w780{p_path}" if p_path else None
                        title = res.get('title') or res.get('name') or query.upper()
                        rating = res.get('vote_average', 'N/A')
                        year = (res.get('release_date') or res.get('first_air_date') or "0000")[:4]
                        return poster, title, rating, year
    except: pass
    return None, query.upper(), "N/A", "0000"

async def delete_after_delay(msgs, delay):
    await asyncio.sleep(delay)
    for m in msgs:
        try: await m.delete()
        except: pass

# ================= ADMIN COMMANDS =================

@app.on_message(filters.command("pratap") & filters.user(ADMIN_IDS))
async def stats_cmd(client, msg):
    if client.movies is None:
        err_msg = client.db_error if client.db_error else "Unknown Connection Error"
        return await msg.reply(f"⚠️ **Database Connected Nahi Hai!**\n\n🔍 **Error Reason:** `{err_msg}`")
    
    try:
        count = await client.movies.count_documents({})
        await msg.reply(f"📊 **Total Movies in MongoDB:** `{count}`")
    except Exception as e:
        await msg.reply(f"Error fetching stats: {e}")

@app.on_message(filters.command("shortlink") & filters.user(ADMIN_IDS))
async def toggle_shortlink_cmd(client, msg):
    global SHORTLINK_ENABLED
    if len(msg.command) < 2:
        return await msg.reply("Usage: `/shortlink on` or `/shortlink off`")
    
    choice = msg.command[1].lower()
    if choice == "on":
        SHORTLINK_ENABLED = True
        await msg.reply("✅ Shortlink has been **ENABLED**.")
    elif choice == "off":
        SHORTLINK_ENABLED = False
        await msg.reply("❌ Shortlink has been **DISABLED**.")

@app.on_message(filters.command("del") & filters.user(ADMIN_IDS))
async def delete_movie_cmd(client, msg):
    if client.movies is None:
        return await msg.reply("⚠️ Database Connect Nahi Hai.")

    if len(msg.command) < 2:
        return await msg.reply("Usage: `/del movie_name`")
    query = " ".join(msg.command[1:])
    try:
        result = await client.movies.delete_many({"title": {"$regex": query, "$options": "i"}})
        await msg.reply(f"🗑️ `{result.deleted_count}` movies removed matching `{query}`.")
    except Exception as e:
        await msg.reply(f"Error: {e}")

# ================= STORAGE INDEXING & NOTIFICATION =================

@app.on_message(filters.chat(STORAGE_CHANNEL) & (filters.video | filters.document))
async def add_to_db(client, msg):
    if client.movies is None:
        return 
        
    file = msg.video or msg.document
    if not file: return

    # Save karte time original naam ko clean karke save karenge
    raw_name = msg.caption or file.file_name or "Unknown"
    title = clean_name(raw_name)
    
    movie_data = {
        "title": title,
        "raw_name": raw_name, # Original naam bhi save kar rahe hain backup ke liye
        "file_id": file.file_id,
        "caption": msg.caption or title
    }
    
    try:
        await client.movies.insert_one(movie_data)
        await msg.reply_text(f"✅ **Movie Added:** `{title}`")
        
        if LOG_CHANNEL:
            await client.send_message(
                LOG_CHANNEL,
                f"🎬 **New Movie Added!**\n\n"
                f"📛 **Name:** `{title}`\n"
                f"✅ **Status:** Uploaded to Database\n"
                f"🤖 **Bot:** @{client.bot_info.username}"
            )
    except Exception as e:
        print(f"DB Insert Error: {e}")

# ================= SEARCH LOGIC (IMPROVED) =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start", "pratap", "shortlink", "del"]))
async def search_movie(client, msg):
    query = clean_name(msg.text)
    if len(query) < 2: return # Kam se kam 2 akshar hone chahiye

    try: await msg.delete()
    except: pass

    u_name = msg.from_user.first_name if msg.from_user else "User"
    u_id = msg.from_user.id if msg.from_user else "N/A"

    sm = await client.send_message(msg.chat.id, f"🔍 **Searching:** `{msg.text}`...")

    if client.movies is None:
        await sm.edit("❌ **Error:** Database Connected Nahi Hai. Admin ko batayein.")
        asyncio.create_task(delete_after_delay([sm], 15))
        return

    # --- STEP 1: Direct Database Search (Fastest) ---
    try:
        res = await client.movies.find_one({"title": {"$regex": query, "$options": "i"}})
    except:
        res = None

    # --- STEP 2: Fuzzy Logic (Agar direct nahi mila to spelling fix karega) ---
    if not res:
        # DB se saare titles late hain match karne ke liye
        all_titles = []
        try:
            # Sirf title field laayenge taaki fast ho
            async for m in client.movies.find({}, {"title": 1}):
                if "title" in m:
                    all_titles.append(m["title"])
            
            # Difflib se milti julti spelling dhoondenge (Cutoff 0.6 matlab 60% match hona chahiye)
            matches = difflib.get_close_matches(query, all_titles, n=1, cutoff=0.5)
            
            if matches:
                correct_name = matches[0]
                await sm.edit(f"💡 **Did you mean:** `{correct_name}`?\n♻️ **Result Found!**")
                # Sahi naam milne par wapis DB me search
                res = await client.movies.find_one({"title": correct_name})
        except Exception as e:
            print(f"Fuzzy Error: {e}")

    # --- Result Processing ---
    if not res:
        await sm.edit(f"❌ `{msg.text}` nahi mili!\n\n💡 **Tip:** Movie ka sahi naam likhein ya Year check karein.")
        asyncio.create_task(delete_after_delay([sm], 15))
        return 

    db_id = str(res["_id"]) 
    db_title = res["title"]
    
    # TMDB se poster lenge (Original query use karenge taaki TMDB confuse na ho)
    poster, m_title, m_rating, m_year = await get_tmdb_info(db_title)

    bot_url = f"https://t.me/{client.bot_info.username}?start=file_{db_id}"
    final_link = await get_shortlink(bot_url)

    btn = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 DOWNLOAD / WATCH NOW", url=final_link)],
                                [InlineKeyboardButton("✨ JOIN CHANNEL ✨", url=MAIN_CHANNEL_LINK)]])

    cap = (f"✅ **Movie Mil Gayi!**\n\n🎬 **Naam:** `{db_title}`\n"
           f"🌟 **Rating:** `{m_rating}` | 📅 **Year:** `{m_year}`\n\n"
           f"👤 **User:** {u_name}\n🆔 **ID:** `{u_id}`\n\n"
           f"👁️ 2  [Movies 2026 - Cinema Pratap\"❤️🌹]({MAIN_CHANNEL_LINK})")

    if poster:
        res_msg = await client.send_photo(msg.chat.id, poster, caption=cap, reply_markup=btn)
    else:
        res_msg = await client.send_message(msg.chat.id, cap, reply_markup=btn)
    
    await sm.delete()
    asyncio.create_task(delete_after_delay([res_msg], 120))

# ================= START / FSUB =================

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, msg):
    user_id = msg.from_user.id
    
    # Force Sub Check
    if FSUB_CHANNEL != 0:
        try:
            await client.get_chat_member(FSUB_CHANNEL, user_id)
        except UserNotParticipant:
            try:
                invite = (await client.get_chat(FSUB_CHANNEL)).invite_link
            except:
                invite = MAIN_CHANNEL_LINK 
            
            btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 JOIN CHANNEL FIRST 📢", url=invite)],
                                        [InlineKeyboardButton("✅ TRY AGAIN", url=f"https://t.me/{client.bot_info.username}?start={msg.command[1] if len(msg.command)>1 else ''}")]])
            return await msg.reply("❌ **Access Denied!**\n\nFile paane ke liye pehle niche diye gaye channel ko join karein.", reply_markup=btn)
        except Exception as e:
            print(f"Join Check Error: {e}")

    if len(msg.command) < 2:
        return await msg.reply("👋 Namaste! Group me movie search karein.")

    data = msg.command[1]
    if data.startswith("file_"):
        if client.movies is None:
            return await msg.reply("❌ Database disconnected.")
            
        m_id = data.split("_")[1]
        
        try:
            res = await client.movies.find_one({"_id": ObjectId(m_id)})
        except:
            return await msg.reply("❌ Invalid Link or File Removed.")
        
        if res:
            f_id = res["file_id"]
            title = res["title"]
            caption = (f"📂 **File Name:** `{title}`\n👤 **Admin:** pratap 🇮🇳❤️\n\n"
                       f"🚀 **Channel:** {MAIN_CHANNEL_LINK}\n\n"
                       f"👁️ 2  [Movies 2026 - Cinema Pratap\"❤️🌹]({MAIN_CHANNEL_LINK})\n\n"
                       f"⚠️ **Warning:** 2 minute me delete ho jayegi!")
            
            sf = await client.send_cached_media(msg.chat.id, f_id, caption=caption)
            asyncio.create_task(delete_after_delay([sf], 120))
        else:
            await msg.reply("❌ File Not Found in Database.")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(start_web_server())
    app.run()
