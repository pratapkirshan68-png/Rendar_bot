import os
import re
import asyncio
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId

# ================= CONFIGURATION (Purely from Render) =================

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URL = os.environ.get("MONGO_URL")

# Ye IDs ab Render ke "Environment Variables" se uthayi jayengi
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS").split()]
STORAGE_CHANNEL = int(os.environ.get("STORAGE_CHANNEL"))
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL"))
SEARCH_CHAT = int(os.environ.get("SEARCH_CHAT"))
FSUB_CHANNEL = int(os.environ.get("FSUB_CHANNEL"))

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
SHORT_DOMAIN = os.environ.get("SHORT_DOMAIN")
SHORT_API_KEY = os.environ.get("SHORT_API_KEY")
MAIN_CHANNEL_LINK = os.environ.get("MAIN_CHANNEL_LINK")

# Toggle for Shortener
SHORTLINK_ENABLED = True 

app = Client("pratap_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

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

async def auto_delete(msg):
    await asyncio.sleep(120) # 2 minutes delete timer
    try: await msg.delete()
    except: pass

# ================= SEARCH LOGIC =================

@app.on_message(filters.chat(SEARCH_CHAT) & filters.text & ~filters.command(["start"]))
async def search_movie(client, msg):
    query = msg.text.strip().lower()
    if len(query) < 2: return

    db_client = AsyncIOMotorClient(MONGO_URL)
    collection = db_client["PratapCinemaBot"]["movies"]
    
    # Keyword search logic
    keywords = query.split()
    regex_pattern = ".*".join(keywords)
    cursor = collection.find({"title": {"$regex": regex_pattern, "$options": "i"}})
    results = await cursor.to_list(length=20)

    if not results: return

    buttons = []
    me = await client.get_me()
    for item in results:
        bot_url = f"https://t.me/{me.username}?start=file_{str(item['_id'])}"
        short_link = await get_shortlink(bot_url)
        buttons.append([InlineKeyboardButton(f"🎬 {item['title']}", url=short_link)])

    buttons.append([InlineKeyboardButton("✨ JOIN CHANNEL ✨", url=MAIN_CHANNEL_LINK)])
    
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
