import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, BotCommand
)
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web

# ============ SOZLAMALAR ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "BOT_TOKEN_BU_YERGA")
INITIAL_ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "8470314807").split(",")]
DB_PATH = "anime_bot.db"

PAYMENT_CARD = "7777 0105 7309 7248"
PAYMENT_CARD_OWNER = "Paynet virtual karta"
ADMIN_USERNAME = "@Anituz_org"
VIP_PLANS = [
    ("1 oylik VIP", "15 000 so'm"),
    ("2 oylik VIP", "29 000 so'm"),
    ("3 oylik VIP", "39 000 so'm"),
    ("6 oylik VIP", "100 000 so'm"),
    ("8 oylik VIP", "140 000 so'm"),
]
MONTH_TO_DAYS = {"1oy": 30, "2oy": 60, "3oy": 90, "6oy": 180, "8oy": 240}

ANNOUNCE_CHANNEL_ID = os.environ.get("ANNOUNCE_CHANNEL_ID", "-1004379465750")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Major_dubbingbot")
PORT = int(os.environ.get("PORT", 10000))
ONLINE_WINDOW_MIN = 5  # "online" deb hisoblanadigan faollik oynasi (daqiqa)

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)


class SearchStates(StatesGroup):
    waiting_query = State()


class AdminFSM(StatesGroup):
    waiting_new_admin_id = State()
    waiting_ad_channel = State()
    waiting_delete_target = State()


# ============ DATABASE ============
def db_init():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS animes (
        code TEXT PRIMARY KEY, title TEXT, total_seasons INTEGER DEFAULT 1,
        quality TEXT, genre TEXT, rating TEXT,
        views INTEGER DEFAULT 0, downloads INTEGER DEFAULT 0, is_premium INTEGER DEFAULT 0,
        poster_file_id TEXT, added_date TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS episodes (
        anime_code TEXT, season_number INTEGER, episode_number INTEGER, file_id TEXT,
        PRIMARY KEY (anime_code, season_number, episode_number)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, joined_date TEXT,
        is_vip INTEGER DEFAULT 0, vip_until TEXT, last_seen TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS history (
        user_id INTEGER, anime_code TEXT, season_number INTEGER, episode_number INTEGER, watched_date TEXT,
        PRIMARY KEY (user_id, anime_code, season_number, episode_number)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY, added_date TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT
    )""")
    cur.execute("SELECT COUNT(*) FROM admins")
    if cur.fetchone()[0] == 0:
        for aid in INITIAL_ADMIN_IDS:
            cur.execute("INSERT OR IGNORE INTO admins (user_id, added_date) VALUES (?,?)",
                        (aid, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def db(query, params=(), fetch=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(query, params)
    result = None
    if fetch == "one":
        result = cur.fetchone()
    elif fetch == "all":
        result = cur.fetchall()
    conn.commit()
    conn.close()
    return result


def is_admin(user_id: int) -> bool:
    return bool(db("SELECT 1 FROM admins WHERE user_id=?", (user_id,), "one"))


def ensure_user(user_id: int, username: str):
    now = datetime.now().isoformat()
    if not db("SELECT 1 FROM users WHERE user_id=?", (user_id,), "one"):
        db("INSERT INTO users (user_id, username, joined_date, last_seen) VALUES (?,?,?,?)",
           (user_id, username or "", now, now))
    else:
        db("UPDATE users SET last_seen=? WHERE user_id=?", (now, user_id))


def is_vip(user_id: int) -> bool:
    row = db("SELECT is_vip, vip_until FROM users WHERE user_id=?", (user_id,), "one")
    if not row or not row[0]:
        return False
    if row[1] and row[1] != "forever":
        if datetime.fromisoformat(row[1]) < datetime.now():
            return False
    return True


def get_setting(key: str, default: str = "") -> str:
    row = db("SELECT value FROM settings WHERE key=?", (key,), "one")
    return row[0] if row else default


def set_setting(key: str, value: str):
    db("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))


# ============ KLAVIATURALAR ============
def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👑 Admin qo'shish/olish")],
            [KeyboardButton(text="📢 Kanal reklama")],
            [KeyboardButton(text="🎬 Anime qo'shish"), KeyboardButton(text="🗑 Anime/qism o'chirish")],
            [KeyboardButton(text="➕ Qism qo'shish")],
            [KeyboardButton(text="👥 Foydalanuvchilar"), KeyboardButton(text="📊 Statistika")],
            [KeyboardButton(text="📈 Kunlik reyting")],
        ],
        resize_keyboard=True,
    )


def dashboard_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔢 Kod orqali qidirish", callback_data="search_mode:code")],
        [InlineKeyboardButton(text="🖼 Rasm orqali qidirish", callback_data="search_mode:image")],
        [InlineKeyboardButton(text="📝 Nomi orqali qidirish", callback_data="search_mode:name")],
    ])


def seasons_keyboard(code: str, total_seasons: int) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=f"🎞 {s}-fasl", callback_data=f"season:{code}:{s}")]
               for s in range(1, total_seasons + 1)]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def episodes_keyboard(code: str, season: int, total_ep: int) -> InlineKeyboardMarkup:
    buttons, row = [], []
    for i in range(1, total_ep + 1):
        row.append(InlineKeyboardButton(text=f"▶️{i}", callback_data=f"ep:{code}:{season}:{i}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def episode_count_kb(code: str, season: int) -> InlineKeyboardMarkup:
    total_ep = db("SELECT COUNT(*) FROM episodes WHERE anime_code=? AND season_number=?",
                  (code, season), "one")[0]
    return episodes_keyboard(code, season, total_ep)


# ============ STATISTIKA HISOBLASH ============
def compute_dashboard_stats():
    total_animes = db("SELECT COUNT(*) FROM animes", (), "one")[0]
    total_episodes = db("SELECT COUNT(*) FROM episodes", (), "one")[0]
    total_users = db("SELECT COUNT(*) FROM users", (), "one")[0]
    cutoff = (datetime.now() - timedelta(minutes=ONLINE_WINDOW_MIN)).isoformat()
    online = db("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (cutoff,), "one")[0]
    return total_animes, total_episodes, total_users, online


async def send_dashboard(message: Message):
    total_animes, total_episodes, total_users, online = compute_dashboard_stats()
    text = (
        "🎬 <b>Anime botiga xush kelibsiz!</b>\n\n"
        f"📚 Animelar: <b>{total_animes}</b> ta\n"
        f"🎞 Jami qismlar: <b>{total_episodes}</b> ta\n"
        f"👥 Foydalanuvchilar: <b>{total_users}</b> ta\n"
        f"🟢 Hozir online: <b>{online}</b> kishi\n\n"
        "Anime topish uchun quyidagidan birini tanlang, "
        "yoki shunchaki anime kodini yozib yuboring 👇"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=dashboard_inline())


# ============ ANIME KARTOCHKASI ============
async def show_anime(message: Message, code: str, user_id: int):
    anime = db("SELECT * FROM animes WHERE code=?", (code,), "one")
    if not anime:
        await message.answer("❌ Bunday kodli/nomli anime topilmadi.")
        return

    (acode, title, total_seasons, quality, genre, rating, views, downloads,
     is_premium, poster_file_id, added_date) = anime

    db("UPDATE animes SET views = views + 1 WHERE code=?", (acode,))
    ad_channel = get_setting("ad_channel", "")

    caption = (
        f"🎬 <b>{title}</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"🎞 Fasllar soni: {total_seasons}\n"
        f"💿 Sifati: {quality}\n"
        f"⭐️ Reyting: {rating or '—'}\n"
        f"🏷 Janri: {genre}\n"
    )
    if ad_channel:
        caption += f"📢 Kanal: {ad_channel}\n"
    caption += (
        "━━━━━━━━━━━━━━━\n"
        f"👀 Ko'rilgan: {views + 1} marta\n"
        f"⬇️ Yuklab olingan: {downloads} marta"
    )

    locked = is_premium and not is_vip(user_id)
    if locked:
        caption += "\n\n💎 <b>Bu — faqat VIP foydalanuvchilar uchun anime!</b>\n/vip buyrug'i orqali VIP bo'ling."

    kb = None
    if not locked:
        kb = seasons_keyboard(acode, total_seasons) if total_seasons > 1 else episode_count_kb(acode, 1)

    try:
        if poster_file_id:
            await message.answer_photo(poster_file_id, caption=caption, parse_mode="HTML", reply_markup=kb)
        else:
            await message.answer(caption, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        logging.error(e)
        await message.answer(caption, parse_mode="HTML", reply_markup=kb)


# ============ /START ============
@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user.id, message.from_user.username)
    admin = is_admin(message.from_user.id)

    if command.args:
        code = command.args.strip()
        if db("SELECT 1 FROM animes WHERE code=?", (code,), "one"):
            if admin:
                await message.answer("🛠 Admin panel faol.", reply_markup=admin_menu())
            await show_anime(message, code, message.from_user.id)
            return

    if admin:
        await message.answer("🛠 Xush kelibsiz, admin!", reply_markup=admin_menu())
    else:
        await message.answer("🎬 Xush kelibsiz!", reply_markup=ReplyKeyboardRemove())
    await send_dashboard(message)


# ============ QIDIRISH ============
@router.callback_query(F.data.startswith("search_mode:"))
async def cb_search_mode(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.split(":")[1]
    if mode == "image":
        await callback.message.answer(
            "🖼 Hozircha rasm orqali avtomatik tanish qo'llab-quvvatlanmaydi.\n"
            "Iltimos, anime nomini yozib yuboring — shunga yaqin nomlarni topib beraman:"
        )
    elif mode == "code":
        await callback.message.answer("🔢 Anime kodini yuboring:")
    else:
        await callback.message.answer("📝 Anime nomini yuboring:")
    await state.set_state(SearchStates.waiting_query)
    await callback.answer()


@router.message(StateFilter(SearchStates.waiting_query))
async def process_search(message: Message, state: FSMContext):
    await state.clear()
    query = message.text.strip()
    ensure_user(message.from_user.id, message.from_user.username)
    await _do_search(message, query)


async def _do_search(message: Message, query: str):
    exact = db("SELECT code FROM animes WHERE code=?", (query,), "one")
    if exact:
        await show_anime(message, exact[0], message.from_user.id)
        return
    matches = db("SELECT code, title FROM animes WHERE title LIKE ?", (f"%{query}%",), "all")
    if not matches:
        await message.answer("❌ Hech narsa topilmadi.")
        return
    if len(matches) == 1:
        await show_anime(message, matches[0][0], message.from_user.id)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=f"open:{c}")] for c, t in matches[:15]
    ])
    await message.answer("Bir nechta natija topildi, birini tanlang:", reply_markup=kb)


@router.callback_query(F.data.startswith("open:"))
async def cb_open(callback: CallbackQuery):
    code = callback.data.split(":")[1]
    await show_anime(callback.message, code, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data.startswith("season:"))
async def cb_season(callback: CallbackQuery):
    _, code, season = callback.data.split(":")
    kb = episode_count_kb(code, int(season))
    await callback.message.answer(f"🎞 {season}-fasl qismlari:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("ep:"))
async def cb_episode(callback: CallbackQuery):
    _, code, season, num = callback.data.split(":")
    season, num = int(season), int(num)
    ensure_user(callback.from_user.id, callback.from_user.username)
    anime = db("SELECT is_premium FROM animes WHERE code=?", (code,), "one")
    if anime and anime[0] and not is_vip(callback.from_user.id):
        await callback.answer("💎 Bu qism faqat VIP uchun!", show_alert=True)
        return
    ep = db("SELECT file_id FROM episodes WHERE anime_code=? AND season_number=? AND episode_number=?",
            (code, season, num), "one")
    if not ep:
        await callback.answer("❌ Bu qism hali yuklanmagan.", show_alert=True)
        return
    try:
        await bot.send_video(callback.message.chat.id, ep[0])
        db("UPDATE animes SET downloads = downloads + 1 WHERE code=?", (code,))
        db("INSERT OR REPLACE INTO history (user_id, anime_code, season_number, episode_number, watched_date) VALUES (?,?,?,?,?)",
           (callback.from_user.id, code, season, num, datetime.now().isoformat()))
    except TelegramBadRequest as e:
        logging.error(e)
        await callback.answer("⚠️ Xatolik yuz berdi.", show_alert=True)
        return
    await callback.answer()


# ============ FOYDALANUVCHI BUYRUQLARI ============
@router.message(Command("vip"))
async def cmd_vip(message: Message):
    plans_text = "\n".join([f"• {name} — {price}" for name, price in VIP_PLANS])
    await message.answer(
        f"💎 <b>VIP tariflar</b>\n\n{plans_text}\n\n"
        f"💳 To'lov uchun karta: <code>{PAYMENT_CARD}</code>\n"
        f"👤 Karta egasi: {PAYMENT_CARD_OWNER}\n\n"
        f"To'lovni amalga oshirgach, chekni {ADMIN_USERNAME} ga yuboring.",
        parse_mode="HTML",
    )


@router.message(Command("reklama"))
async def cmd_reklama(message: Message):
    await message.answer(
        f"📢 Kanalingizni reklama qildirmoqchi bo'lsangiz, shu adminga yozing:\n{ADMIN_USERNAME}"
    )


# ============ ADMIN: DASHBOARD TUGMALARI ============
@router.message(F.text == "👑 Admin qo'shish/olish")
async def btn_admin_manage(message: Message):
    if not is_admin(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Admin qo'shish", callback_data="adm:add")],
        [InlineKeyboardButton(text="📋 Adminlar ro'yxati / olib tashlash", callback_data="adm:list")],
    ])
    await message.answer("👑 Admin boshqaruvi:", reply_markup=kb)


@router.callback_query(F.data == "adm:add")
async def cb_admin_add(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.answer("Yangi admin qilmoqchi bo'lgan odamning Telegram ID raqamini yuboring:")
    await state.set_state(AdminFSM.waiting_new_admin_id)
    await callback.answer()


@router.message(StateFilter(AdminFSM.waiting_new_admin_id))
async def process_new_admin(message: Message, state: FSMContext):
    await state.clear()
    try:
        new_id = int(message.text.strip())
    except ValueError:
        await message.answer("❗️ Faqat raqam (ID) yuboring.")
        return
    db("INSERT OR IGNORE INTO admins (user_id, added_date) VALUES (?,?)", (new_id, datetime.now().isoformat()))
    await message.answer(f"✅ {new_id} endi admin!")
    try:
        await bot.send_message(new_id, "🎉 Sizga admin huquqi berildi!", reply_markup=admin_menu())
    except Exception:
        pass


@router.callback_query(F.data == "adm:list")
async def cb_admin_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    admins = db("SELECT user_id FROM admins", (), "all")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"👤 {aid}", callback_data=f"adm:sel:{aid}")] for (aid,) in admins
    ])
    await callback.message.answer("📋 Hozirgi adminlar:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:sel:"))
async def cb_admin_select(callback: CallbackQuery):
    target_id = callback.data.split(":")[2]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Admin olib tashlash", callback_data=f"adm:rm:{target_id}")],
        [InlineKeyboardButton(text="✅ Yo'q, admin qolsin", callback_data="adm:keep")],
    ])
    await callback.message.answer(f"👤 ID: {target_id}\nNima qilamiz?", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:rm:"))
async def cb_admin_remove(callback: CallbackQuery):
    target_id = int(callback.data.split(":")[2])
    db("DELETE FROM admins WHERE user_id=?", (target_id,))
    await callback.message.answer(f"🗑 {target_id} admin huquqidan olib tashlandi.")
    await callback.answer()


@router.callback_query(F.data == "adm:keep")
async def cb_admin_keep(callback: CallbackQuery):
    await callback.message.answer("✅ O'zgarishsiz qoldi.")
    await callback.answer()


# ============ ADMIN: KANAL REKLAMA ============
@router.message(F.text == "📢 Kanal reklama")
async def btn_ad_channel(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    current = get_setting("ad_channel", "— sozlanmagan —")
    await message.answer(
        f"📢 Hozirgi anime kartochkalarida ko'rsatiladigan kanal: {current}\n\n"
        "Yangi kanal username'ini yuboring (masalan @anistarkuz):"
    )
    await state.set_state(AdminFSM.waiting_ad_channel)


@router.message(StateFilter(AdminFSM.waiting_ad_channel))
async def process_ad_channel(message: Message, state: FSMContext):
    await state.clear()
    set_setting("ad_channel", message.text.strip())
    await message.answer(f"✅ Kanal reklamasi o'rnatildi: {message.text.strip()}")


# ============ ADMIN: FOYDALANUVCHILAR / STATISTIKA / REYTING ============
@router.message(F.text == "👥 Foydalanuvchilar")
async def btn_users(message: Message):
    if not is_admin(message.from_user.id):
        return
    total = db("SELECT COUNT(*) FROM users", (), "one")[0]
    cutoff = (datetime.now() - timedelta(minutes=ONLINE_WINDOW_MIN)).isoformat()
    online = db("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (cutoff,), "one")[0]
    watching_cutoff = (datetime.now() - timedelta(minutes=ONLINE_WINDOW_MIN)).isoformat()
    watching = db("SELECT COUNT(DISTINCT user_id) FROM history WHERE watched_date >= ?",
                  (watching_cutoff,), "one")[0]
    await message.answer(
        "👥 <b>Foydalanuvchilar</b>\n\n"
        f"🟢 Online (so'nggi {ONLINE_WINDOW_MIN} daqiqa): {online}\n"
        f"▶️ Hozir anime ko'rayotganlar: {watching}\n"
        f"👤 Botda jami: {total} kishi",
        parse_mode="HTML",
    )


@router.message(F.text == "📊 Statistika")
async def btn_stats(message: Message):
    if not is_admin(message.from_user.id):
        return
    animes_count = db("SELECT COUNT(*) FROM animes", (), "one")[0]
    episodes_count = db("SELECT COUNT(*) FROM episodes", (), "one")[0]
    users_count = db("SELECT COUNT(*) FROM users", (), "one")[0]
    admins_count = db("SELECT COUNT(*) FROM admins", (), "one")[0]
    vip_count = db("SELECT COUNT(*) FROM users WHERE is_vip=1", (), "one")[0]
    await message.answer(
        "📊 <b>Bot statistikasi</b>\n\n"
        f"🎬 Animelar: {animes_count}\n"
        f"🎞 Jami qismlar: {episodes_count}\n"
        f"👥 Foydalanuvchilar: {users_count}\n"
        f"👑 Adminlar: {admins_count}\n"
        f"💎 VIP: {vip_count}",
        parse_mode="HTML",
    )


@router.message(F.text == "📈 Kunlik reyting")
async def btn_daily(message: Message):
    if not is_admin(message.from_user.id):
        return
    today = datetime.now().date().isoformat()
    new_today = db("SELECT COUNT(*) FROM users WHERE joined_date LIKE ?", (f"{today}%",), "one")[0]
    total = db("SELECT COUNT(*) FROM users", (), "one")[0]
    await message.answer(
        "📈 <b>Kunlik reyting</b>\n\n"
        f"📅 Bugun qo'shilgan yangi foydalanuvchilar: {new_today}\n"
        f"👥 Jami foydalanuvchilar: {total}\n\n"
        "ℹ️ Eslatma: Telegram bot API foydalanuvchi botni \"tark etganini\" aniq ko'rsatmaydi "
        "(faqat botni bloklaganini bilsa bo'ladi), shuning uchun bu yerda faqat yangi qo'shilganlar soni ko'rsatilmoqda.",
        parse_mode="HTML",
    )


# ============ ADMIN: ANIME QO'SHISH ============
@router.message(F.text == "🎬 Anime qo'shish")
async def btn_add_anime(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🖼 Anime uchun rasm (poster) yuboring, rasm ostiga (caption) shu shaklda yozing:\n\n"
        "<code>Kod: 25\n"
        "Nomi: Anime nomi\n"
        "Fasllar: 3\n"
        "Sifat: 720p\n"
        "Reyting: 8.5\n"
        "Janr: Isekai, jangari\n"
        "Premium: yo'q</code>\n\n"
        "⚠️ \"Kod\" qatori — bu ichki, texnik kod, u foydalanuvchilarga hech qachon ko'rsatilmaydi. "
        "Har bir kod faqat bitta animega tegishli bo'lishi kerak.",
        parse_mode="HTML",
    )


@router.message(F.photo, F.caption.contains("Kod:"))
async def process_addanime(message: Message):
    if not is_admin(message.from_user.id):
        return
    fields = {}
    for line in message.caption.split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            fields[key.strip().lower()] = val.strip()

    try:
        code = fields["kod"]
        title = fields["nomi"]
        total_seasons = int(fields.get("fasllar", "1"))
        quality = fields.get("sifat", "-")
        rating = fields.get("reyting", "-")
        genre = fields.get("janr", "-")
        is_premium = 1 if fields.get("premium", "yo'q").lower() in ("ha", "premium", "vip") else 0
    except (KeyError, ValueError) as e:
        await message.answer(f"❌ Ma'lumot to'liq emas: {e}")
        return

    existing = db("SELECT title FROM animes WHERE code=?", (code,), "one")
    if existing and existing[0] != title:
        await message.answer(
            f"⚠️ Bu kod (\"{code}\") allaqachon \"{existing[0]}\" animesi uchun band. "
            "Boshqa kod tanlang."
        )
        return

    poster_file_id = message.photo[-1].file_id
    db("""INSERT OR REPLACE INTO animes
          (code, title, total_seasons, quality, genre, rating,
           views, downloads, is_premium, poster_file_id, added_date)
          VALUES (?,?,?,?,?,?,COALESCE((SELECT views FROM animes WHERE code=?),0),
                  COALESCE((SELECT downloads FROM animes WHERE code=?),0),?,?,?)""",
       (code, title, total_seasons, quality, genre, rating, code, code, is_premium, poster_file_id,
        datetime.now().isoformat()))

    await message.answer(
        f"✅ Anime qo'shildi!\nKod: <code>{code}</code>\n\n"
        f"Endi \"➕ Qism qo'shish\" tugmasi orqali qismlarini yuklang.",
        parse_mode="HTML",
    )

    if ANNOUNCE_CHANNEL_ID and BOT_USERNAME:
        announce_caption = (
            f"🎬 {title}\n"
            f"🎞 Fasllar: {total_seasons}\n"
            f"💿 Sifat: {quality} | 🏷 Janr: {genre}\n"
            f"⭐️ Reyting: {rating}"
        )
        watch_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="▶️ Anime ko'rish", url=f"https://t.me/{BOT_USERNAME}?start={code}")
        ]])
        try:
            await bot.send_photo(int(ANNOUNCE_CHANNEL_ID), poster_file_id,
                                  caption=announce_caption, reply_markup=watch_kb)
        except TelegramBadRequest as e:
            logging.error(f"Announce error: {e}")
            await message.answer("⚠️ Anime qo'shildi, lekin kanalga e'lon qilishda xatolik (bot kanalga admin ekanini tekshiring).")


# ============ ADMIN: QISM QO'SHISH ============
@router.message(F.text == "➕ Qism qo'shish")
async def btn_add_episode(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "➕ Qism qo'shishning 2 usuli bor:\n\n"
        "1) Videoni to'g'ridan-to'g'ri botga yuboring, caption qismiga yozing:\n"
        "<code>/addep KOD FASL QISM</code>\n\n"
        "2) Videoni (boshqa botdan/kanaldan) botga <b>forward</b> qiling, "
        "so'ng o'sha forward qilingan xabarga <b>javob (reply)</b> tariqasida yozing:\n"
        "<code>/addep KOD FASL QISM</code>",
        parse_mode="HTML",
    )


@router.message(F.video, F.caption, F.caption.startswith("/addep"))
async def process_addep_direct(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.caption.split()
    if len(parts) != 4:
        await message.answer("❗️ Format: /addep KOD FASL QISM  (masalan: /addep 25 1 1)")
        return
    _, code, season, num = parts
    try:
        season, num = int(season), int(num)
    except ValueError:
        await message.answer("❗️ Fasl va qism raqam bo'lishi kerak.")
        return
    if not db("SELECT 1 FROM animes WHERE code=?", (code,), "one"):
        await message.answer("❌ Bunday kodli anime topilmadi, avval anime qo'shing.")
        return
    file_id = message.video.file_id
    db("INSERT OR REPLACE INTO episodes (anime_code, season_number, episode_number, file_id) VALUES (?,?,?,?)",
       (code, season, num, file_id))
    await message.answer(f"✅ {code}-anime, {season}-fasl, {num}-qism qo'shildi!")


@router.message(Command("addep"))
async def cmd_addep_reply(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if message.reply_to_message and message.reply_to_message.video and command.args:
        parts = command.args.split()
        if len(parts) != 3:
            await message.answer("❗️ Format: /addep KOD FASL QISM")
            return
        code, season, num = parts
        try:
            season, num = int(season), int(num)
        except ValueError:
            await message.answer("❗️ Fasl va qism raqam bo'lishi kerak.")
            return
        if not db("SELECT 1 FROM animes WHERE code=?", (code,), "one"):
            await message.answer("❌ Bunday kodli anime topilmadi.")
            return
        file_id = message.reply_to_message.video.file_id
        db("INSERT OR REPLACE INTO episodes (anime_code, season_number, episode_number, file_id) VALUES (?,?,?,?)",
           (code, season, num, file_id))
        await message.answer(f"✅ {code}-anime, {season}-fasl, {num}-qism qo'shildi!")
        return
    await message.answer(
        "❗️ Videoni caption bilan yuboring (/addep KOD FASL QISM), "
        "yoki forward qilingan videoga javoban shu buyruqni yozing."
    )


# ============ ADMIN: ANIME / QISM O'CHIRISH ============
@router.message(F.text == "🗑 Anime/qism o'chirish")
async def btn_delete(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🗑 Butun animeni o'chirish uchun — anime kodi yoki nomini yuboring.\n"
        "Faqat bitta qismni o'chirish uchun — <code>KOD FASL QISM</code> shaklida yuboring.\n"
        "Masalan: <code>25</code> (butun anime) yoki <code>25 1 3</code> (faqat 1-fasl 3-qism)",
        parse_mode="HTML",
    )
    await state.set_state(AdminFSM.waiting_delete_target)


@router.message(StateFilter(AdminFSM.waiting_delete_target))
async def process_delete(message: Message, state: FSMContext):
    await state.clear()
    parts = message.text.strip().split()

    if len(parts) == 3:
        code, season, num = parts
        try:
            season, num = int(season), int(num)
        except ValueError:
            await message.answer("❗️ Fasl va qism raqam bo'lishi kerak.")
            return
        db("DELETE FROM episodes WHERE anime_code=? AND season_number=? AND episode_number=?",
           (code, season, num))
        await message.answer(f"🗑 {code}-anime, {season}-fasl, {num}-qism o'chirildi.")
        return

    query = message.text.strip()
    exact = db("SELECT code, title FROM animes WHERE code=?", (query,), "one")
    if exact:
        code = exact[0]
        db("DELETE FROM animes WHERE code=?", (code,))
        db("DELETE FROM episodes WHERE anime_code=?", (code,))
        db("DELETE FROM history WHERE anime_code=?", (code,))
        await message.answer(f"🗑 \"{exact[1]}\" (kod: {code}) butunlay o'chirildi.")
        return

    matches = db("SELECT code, title FROM animes WHERE title LIKE ?", (f"%{query}%",), "all")
    if not matches:
        await message.answer("❌ Bunday anime topilmadi.")
        return
    if len(matches) == 1:
        code, title = matches[0]
        db("DELETE FROM animes WHERE code=?", (code,))
        db("DELETE FROM episodes WHERE anime_code=?", (code,))
        db("DELETE FROM history WHERE anime_code=?", (code,))
        await message.answer(f"🗑 \"{title}\" (kod: {code}) butunlay o'chirildi.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=f"delconfirm:{c}")] for c, t in matches[:15]
    ])
    await message.answer("Bir nechta natija topildi, o'chirmoqchi bo'lganingizni tanlang:", reply_markup=kb)


@router.callback_query(F.data.startswith("delconfirm:"))
async def cb_delconfirm(callback: CallbackQuery):
    code = callback.data.split(":")[1]
    title_row = db("SELECT title FROM animes WHERE code=?", (code,), "one")
    db("DELETE FROM animes WHERE code=?", (code,))
    db("DELETE FROM episodes WHERE anime_code=?", (code,))
    db("DELETE FROM history WHERE anime_code=?", (code,))
    await callback.message.answer(f"🗑 \"{title_row[0] if title_row else code}\" o'chirildi.")
    await callback.answer()


# ============ ADMIN: VIP BERISH VA XABAR ============
@router.message(Command("givevip"))
async def cmd_givevip(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("❗️ Foydalanish: /givevip USER_ID MUDDAT (1oy/2oy/3oy/6oy/8oy/forever)")
        return
    try:
        parts = command.args.split()
        target_id = int(parts[0])
        duration = parts[1]
    except (ValueError, IndexError):
        await message.answer("❗️ Foydalanish: /givevip USER_ID MUDDAT")
        return
    if duration == "forever":
        days = None
    elif duration in MONTH_TO_DAYS:
        days = MONTH_TO_DAYS[duration]
    else:
        await message.answer("❗️ Noto'g'ri muddat. Variantlar: 1oy, 2oy, 3oy, 6oy, 8oy yoki forever")
        return
    ensure_user(target_id, "")
    until = "forever" if days is None else (datetime.now() + timedelta(days=days)).isoformat()
    db("UPDATE users SET is_vip=1, vip_until=? WHERE user_id=?", (until, target_id))
    await message.answer(f"✅ {target_id} foydalanuvchiga VIP berildi ({duration}).")
    try:
        await bot.send_message(target_id, "🎉 Tabriklaymiz! Sizga VIP faollashtirildi.")
    except Exception:
        pass


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("❗️ Foydalanish: /broadcast Xabar matni")
        return
    ids = [r[0] for r in db("SELECT user_id FROM users", (), "all")]
    sent, failed = 0, 0
    status = await message.answer(f"Yuborilmoqda... 0/{len(ids)}")
    for i, uid in enumerate(ids, start=1):
        try:
            await bot.send_message(uid, command.args)
            sent += 1
        except Exception:
            failed += 1
        if i % 25 == 0:
            await status.edit_text(f"Yuborilmoqda... {i}/{len(ids)}")
        await asyncio.sleep(0.05)
    await status.edit_text(f"✅ Yakunlandi. Yuborildi: {sent}, xato: {failed}")


# ============ TO'G'RIDAN-TO'G'RI KOD YOZILSA (menyudan tashqari) ============
@router.message(F.text & ~F.text.startswith("/"))
async def fallback_text(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return  # boshqa handler kutmoqda
    ensure_user(message.from_user.id, message.from_user.username)
    admin_buttons = {
        "👑 Admin qo'shish/olish", "📢 Kanal reklama", "🎬 Anime qo'shish",
        "🗑 Anime/qism o'chirish", "➕ Qism qo'shish", "👥 Foydalanuvchilar",
        "📊 Statistika", "📈 Kunlik reyting",
    }
    if message.text in admin_buttons:
        return
    await _do_search(message, message.text.strip())


# ============ KEEP-ALIVE WEB SERVER ============
async def handle_ping(request):
    return web.Response(text="Anime bot ishlayapti ✅")


async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Keep-alive server {PORT}-portda ishga tushdi")


# ============ ISHGA TUSHIRISH ============
async def main():
    db_init()
    await bot.set_my_commands([
        BotCommand(command="start", description="Botni ishga tushirish"),
        BotCommand(command="vip", description="VIP olish"),
        BotCommand(command="reklama", description="Kanal reklama qilish"),
    ])
    await start_webserver()
    print("Anime bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
