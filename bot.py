import os
import re
import asyncio
import mimetypes
import shutil
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message
from pymongo import MongoClient
from pyrogram import utils as pyroutils

# Fix for large chat IDs
pyroutils.MIN_CHAT_ID = -999999999999
pyroutils.MIN_CHANNEL_ID = -100999999999999

async def bot_run():
    _app = webserver.Application(client_max_size=30000000)
    _app.add_routes(routes)
    return _app

load_dotenv()

# ==== CONFIG ====
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
MONGO_URI = os.getenv("MONGO_URI")
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x.strip()]
DOWNLOAD_PATH = os.getenv("DOWNLOAD_PATH", "downloads")
ARIA2C_PATH = os.getenv("ARIA2C_PATH", "/usr/bin/aria2c")
UPLOAD_CHANNEL = int(os.getenv("UPLOAD_CHANNEL"))
CHANNEL = int(os.getenv("CHANNEL"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "2000"))

from aiohttp import web as webserver

routes = webserver.RouteTableDef()


@routes.get("/", allow_head=True)
async def root_route_handler(request):
    return webserver.json_response("✅ Web Supported . . . ! Bot is running fine! 🚀")

async def start_web_server():
    client = webserver.AppRunner(await bot_run())
    await client.setup()
    bind_address = "0.0.0.0"
    await webserver.TCPSite(client, bind_address, 8080).start()
    print(f"✅ Web server started on http://{bind_address}:8080")


# ==== DATABASE ====
mongo_client = MongoClient(MONGO_URI)
db = mongo_client["torrentbot"]
admin_collection = db["admins"]

existing = admin_collection.find_one({"_id": "admins"})
if not existing:
    admin_collection.insert_one({"_id": "admins", "users": ADMINS})
else:
    all_admins = list(set(existing["users"] + ADMINS))
    admin_collection.update_one({"_id": "admins"}, {"$set": {"users": all_admins}})

def is_admin(user_id: int) -> bool:
    data = admin_collection.find_one({"_id": "admins"})
    return user_id in data["users"]

# ==== GLOBAL ====
active_downloads = {}
download_queue = asyncio.Queue()

app = Client("torrent_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ==== UTILS ====
def make_bar(percent, color="blue"):
    blocks = 10
    filled_count = int((percent / 100) * blocks)
    empty_count = blocks - filled_count
    emojis = {
        "blue": ("🔵", "⚪"),
        "green": ("🟢", "⚪"),
        "star": ("⭐", "✩"),
        "fire": ("🔥", "·"),
    }
    filled_emoji, empty_emoji = emojis.get(color, ("⬛", "⬜"))
    return filled_emoji * filled_count + empty_emoji * empty_count

def clean_filename(name):
    return re.sub(r"(www\.[^\s]+|\#\w+|\|)", "", name).strip()

def find_all_files(path):
    files_list = []
    allowed_exts = ('.mkv', '.mp4', '.mov', '.avi', '.mp3', '.flac', '.zip', '.pdf', '.apk')
    for root, dirs, files in os.walk(path):
        for f in files:
            fpath = os.path.join(root, f)
            if os.path.isfile(fpath):
                mime, _ = mimetypes.guess_type(fpath)
                ext = os.path.splitext(f)[1].lower()
                print(f"Checked: {fpath} | MIME: {mime} | Size: {os.path.getsize(fpath)}")
                if (mime and any(mime.startswith(x) for x in ["video", "audio", "image", "application"])) or ext in allowed_exts:
                    size = os.path.getsize(fpath)
                    if size <= MAX_FILE_SIZE_MB * 1024 * 1024:
                        files_list.append(fpath)
    return sorted(files_list)

# ==== DOWNLOAD HANDLER ====
async def run_aria2c(link_or_path, user_id, progress_msg):
    proc = await asyncio.create_subprocess_exec(
        ARIA2C_PATH,
        "--bt-stop-timeout=10",
        "-d", DOWNLOAD_PATH,
        link_or_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    active_downloads[user_id] = proc
    last_progress = ""

    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text_line = line.decode("utf-8").strip()
            match = re.search(r'(\d+)%', text_line)
            if match:
                percent = match.group(1)
                if percent != last_progress:
                    last_progress = percent
                    bar = make_bar(int(percent), color="blue")
                    try:
                        await progress_msg.edit(f"Downloading 🔥❄️\n{bar}  {percent}%")
                    except:
                        pass

        await asyncio.wait_for(proc.wait(), timeout=300)

    except asyncio.TimeoutError:
        proc.kill()
        stderr_output = await proc.stderr.read()
        stderr_text = stderr_output.decode("utf-8").strip()
        if "No peers" in stderr_text or "fail" in stderr_text.lower():
            await progress_msg.edit("❌ Dead torrent. No seeders or peers.")
        else:
            await progress_msg.edit("⚠️ aria2c timed out. Possible dead torrent.")
        return
    finally:
        active_downloads.pop(user_id, None)

    # ==== Upload Logic ====
    files = find_all_files(DOWNLOAD_PATH)
    if not files:
        await progress_msg.edit("❌ No suitable media file found. Skipping.")
        return

    await progress_msg.edit(f"✅ Download complete. Uploading {len(files)} file(s)...")

    for idx, file_path in enumerate(files, start=1):
        clean_name = clean_filename(os.path.basename(file_path))
        size_mb = os.path.getsize(file_path) / (1024 * 1024)
        caption = f"✅ Uploaded: `{clean_name}`\n📦 Size: {size_mb:.2f} MB"

        try:
            await app.send_document(chat_id=UPLOAD_CHANNEL, document=file_path, caption=caption)
            print(f"✅ Uploaded: {clean_name}")
        except Exception as e:
            await app.send_message(ADMINS[0], f"⚠️ Upload failed for {clean_name}: {e}")

    # Clean up
    shutil.rmtree(DOWNLOAD_PATH)
    os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# ==== QUEUE HANDLER ====
@app.on_message(filters.chat(CHANNEL) & filters.document)
async def handle_channel_torrent(client, message: Message):
    admin_id = ADMINS[0]
    msg = await app.send_message(admin_id, f"📥 Queued: `{message.document.file_name}`")
    await download_queue.put((client, message, msg))

async def queue_worker():
    while True:
        client, message, log_msg = await download_queue.get()
        try:
            file_path = await message.download(file_name="temp.torrent")
            await log_msg.edit("⚙️ Starting download...")
            await run_aria2c(file_path, log_msg.chat.id, log_msg)
            os.remove(file_path)
        except Exception as e:
            await log_msg.edit(f"❌ Error: {e}")
        download_queue.task_done()

# ==== COMMANDS ====
@app.on_message(filters.command("start"))
async def start(client, message: Message):
    await message.reply_text("**👋 Hello!**\n\nSend me a torrent in the channel, and I will upload the files to another channel.")

# ==== MAIN ====
if __name__ == "__main__":
    if not os.path.exists(DOWNLOAD_PATH):
        os.makedirs(DOWNLOAD_PATH)
    loop = asyncio.get_event_loop()
    loop.create_task(queue_worker())
    loop.create_task(start_web_server())  # ✅ Start web server for Koyeb
    app.run()
