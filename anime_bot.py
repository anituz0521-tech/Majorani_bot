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
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web

# ============ SOZLAMALAR ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "BOT_TOKEN_BU_YERGA")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "123456789,8466013755").split(",")]
DB_PATH = "anime_bot.db"

ADMIN_USERNAME = "@Anituz_org"
PAYMENT_CARD = "7777 0105 7309 7248"
PAYMENT_CARD_OWNER = "Paynet virtual karta"
VIP_PLANS = [
    ("1 oylik VIP", "15 000 so'm"),
    ("2 oylik VIP", "29 000 so'm"),
    ("3 oylik VIP", "39 000 so'm"),
    ("6 oylik VIP", "100 000 so'm"),
    ("8 oylik VIP", "140 000 so'm"),
]
ANNOUNCE_CHANNEL_ID = os.environ.get("ANNOUNCE_CHANNEL_ID", "-1004332909062")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Major_dubbingbot")
PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)


class SearchStates(StatesGroup):
    waiting_query = State()


# ============ DATABASE ============
def db_init():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS animes (
        code TEXT PRIMARY KEY, title TEXT, total_seasons INTEGER DEFAULT 1,
        quality TEXT, genre TEXT, rating TEXT,
        views INTEGER DEFAULT 0, is_premium INTEGER DEFAULT 0,
        poster_file_id TEXT, added_date TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS episodes (
        anime_code TEXT, season_number INTEGER, episode_number INTEGER, file_id TEXT,
        PRIMARY KEY (anime_code, season_number, episode_number)
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, joined_date TEXT,
        is_vip INTEGER DEFAULT 0, vip_until TEXT
    )""")
    cur.execute("""CREATE TABLE IF NOT EXISTS history (
        user_id INTEGER, anime_code TEXT, season_number INTEGER, episode_number INTEGER, watched_date TEXT,
        PRIMARY KEY (user_id, anime_code, season_number, episode_number)
    )""")
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


def ensure_user(user_id: int, username: str):
    if not db("SELECT 1 FROM users WHERE user_id=?", (user_id,), "one"):
        db("INSERT INTO users (user_id, username, joined_date) VALUES (?,?,?)",
           (user_id, username or "", datetime.now().isoformat()))


def is_vip(user_id: int) -> bool:
    row = db("SELECT is_vip, vip_until FROM users WHERE user_id=?", (user_id,), "one")
    if not row or not row[0]:
        return False
    if row[1] and row[1] != "forever":
        if datetime.fromisoformat(row[1]) < datetime.now():
            return False
    return True


# ============ KLAVIATURALAR ============
def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Anime qidirish")],
            [KeyboardButton(text="📚 Qo'llanma"), KeyboardButton(text="💵 Reklama va Homiylik")],
            [KeyboardButton(text="💎 VIP olish"), KeyboardButton(text="👤 Profil")],
            [KeyboardButton(text="🕘 Tarix")],
        ],
        resize_keyboard=True,
    )


def seasons_keyboard(code: str, total_seasons: int) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=f"{s}-fasl", callback_data=f"season:{code}:{s}")]
               for s in range(1, total_seasons + 1)]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def episodes_keyboard(code: str, season: int, total_ep: int) -> InlineKeyboardMarkup:
    buttons, row = [], []
    for i in range(1, total_ep + 1):
        row.append(InlineKeyboardButton(text=str(i), callback_data=f"ep:{code}:{season}:{i}"))
        if len(row) == 4:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ============ ANIME KARTOCHKASI ============
async def show_anime(message: Message, code: str, user_id: int):
    anime = db("SELECT * FROM animes WHERE code=?", (code,), "one")
    if not anime:
        await message.answer("❌ Bunday kodli/nomli anime topilmadi.")
        return

    (acode, title, total_seasons, quality, genre, rating, views,
     is_premium, poster_file_id, added_date) = anime

    db("UPDATE animes SET views = views + 1 WHERE code=?", (acode,))

    caption = (
        f"<b>{title}</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"➤ Fasllar soni: {total_seasons}\n"
        f"➤ Sifati: {quality}\n"
        f"➤ Reyting: {rating or '—'}\n"
        f"➤ Janri: {genre}\n"
        "━━━━━━━━━━━━━━━\n"
        f"🔍 Ko'rishlar soni: {views + 1}"
    )
    locked = is_premium and not is_vip(user_id)
    if locked:
        caption += "\n\n💎 <b>Bu — faqat VIP foydalanuvchilar uchun anime!</b>\n\"💎 VIP olish\" tugmasi orqali VIP bo'ling."

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


def episode_count_kb(code: str, season: int) -> InlineKeyboardMarkup:
    total_ep = db("SELECT COUNT(*) FROM episodes WHERE anime_code=? AND season_number=?",
                  (code, season), "one")[0]
    return episodes_keyboard(code, season, total_ep)


# ============ FOYDALANUVCHI HANDLERLARI ============
@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject):
    ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "🎬 Anime botiga xush kelibsiz!\n\nQuyidagi menyudan foydalaning 👇",
        reply_markup=main_menu(),
    )
    # Agar kanal e'lonidagi "Tomosha qilish" tugmasi orqali kirgan bo'lsa (deep link),
    # o'sha anime kartochkasini darhol ochamiz.
    if command.args:
        code = command.args.strip()
        if db("SELECT 1 FROM animes WHERE code=?", (code,), "one"):
            await show_anime(message, code, message.from_user.id)


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    users_count = db("SELECT COUNT(*) FROM users", (), "one")[0]
    animes_count = db("SELECT COUNT(*) FROM animes", (), "one")[0]
    vip_count = db("SELECT COUNT(*) FROM users WHERE is_vip=1", (), "one")[0]
    watchers_count = db("SELECT COUNT(DISTINCT user_id) FROM history", (), "one")[0]
    total_views = db("SELECT COUNT(*) FROM history", (), "one")[0]
    await message.answer(
        "🛠 <b>Admin panel</b>\n\n"
        f"👥 Foydalanuvchilar: {users_count}\n"
        f"👑 Adminlar: {len(ADMIN_IDS)}\n"
        f"🎬 Animelar soni: {animes_count}\n"
        f"👀 Anime ko'rgan (unikal): {watchers_count}\n"
        f"▶️ Jami tomosha qilingan qismlar: {total_views}\n"
        f"💎 VIP foydalanuvchilar: {vip_count}\n\n"
        "<b>Buyruqlar:</b>\n"
        "/addanime — yangi anime qo'shish (poster + ma'lumot)\n"
        "/addep — video yuborib qism qo'shish\n"
        "/delanime KOD — animeni butunlay o'chirish\n"
        "/delep KOD FASL QISM — bitta qismni o'chirish\n"
        "/givevip USER_ID MUDDAT — VIP berish\n"
        "/broadcast MATN — hammaga xabar\n"
        "/stats — qisqa statistika",
        parse_mode="HTML",
    )


@router.message(F.text == "🔍 Anime qidirish")
async def btn_search(message: Message, state: FSMContext):
    await message.answer("Anime nomi yoki kodini yuboring:")
    await state.set_state(SearchStates.waiting_query)


@router.message(StateFilter(SearchStates.waiting_query))
async def process_search(message: Message, state: FSMContext):
    await state.clear()
    query = message.text.strip()
    ensure_user(message.from_user.id, message.from_user.username)

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
        [InlineKeyboardButton(text=f"{t} (kod: {c})", callback_data=f"open:{c}")]
        for c, t in matches[:15]
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
    await callback.message.answer(f"{season}-fasl qismlari:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("ep:"))
async def cb_episode(callback: CallbackQuery):
    _, code, season, num = callback.data.split(":")
    season, num = int(season), int(num)
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
        db("INSERT OR REPLACE INTO history (user_id, anime_code, season_number, episode_number, watched_date) VALUES (?,?,?,?,?)",
           (callback.from_user.id, code, season, num, datetime.now().isoformat()))
    except TelegramBadRequest as e:
        logging.error(e)
        await callback.answer("⚠️ Xatolik yuz berdi.", show_alert=True)
        return
    await callback.answer()


@router.message(F.text == "📚 Qo'llanma")
async def btn_guide(message: Message):
    await message.answer(
        "📚 <b>Botdan foydalanish</b>\n\n"
        "🔍 <b>Anime qidirish</b> — nomi yoki kodi orqali anime toping\n"
        "💎 <b>VIP olish</b> — premium animelarni ko'rish huquqini oling\n"
        "👤 <b>Profil</b> — o'z ma'lumotlaringizni ko'ring\n"
        "🕘 <b>Tarix</b> — qaysi animelarni tomosha qilganingizni ko'ring\n\n"
        "Anime topgach, fasl va qism raqamini tanlang.",
        parse_mode="HTML",
    )


@router.message(F.text == "💵 Reklama va Homiylik")
async def btn_ads(message: Message):
    await message.answer(
        f"💵 <b>Reklama va Homiylik</b>\n\nMurojaat: {ADMIN_USERNAME}",
        parse_mode="HTML",
    )


@router.message(F.text == "💎 VIP olish")
async def btn_vip(message: Message):
    plans_text = "\n".join([f"• {name} — {price}" for name, price in VIP_PLANS])
    await message.answer(
        f"💎 <b>VIP tariflar</b>\n\n{plans_text}\n\n"
        f"💳 To'lov uchun karta: <code>{PAYMENT_CARD}</code>\n"
        f"👤 Karta egasi: {PAYMENT_CARD_OWNER}\n\n"
        f"To'lovni amalga oshirgach, chekni {ADMIN_USERNAME} ga yuboring.",
        parse_mode="HTML",
    )


@router.message(F.text == "👤 Profil")
async def btn_profile(message: Message):
    ensure_user(message.from_user.id, message.from_user.username)
    user = db("SELECT joined_date, is_vip, vip_until FROM users WHERE user_id=?",
              (message.from_user.id,), "one")
    watched = db("SELECT COUNT(DISTINCT anime_code) FROM history WHERE user_id=?",
                 (message.from_user.id,), "one")[0]
    vip_status = "❌ Yo'q"
    if user[1]:
        vip_status = "♾ Cheksiz" if user[2] == "forever" else f"✅ {user[2][:10]} gacha"
    await message.answer(
        f"👤 <b>Profilingiz</b>\n\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n"
        f"💎 VIP holati: {vip_status}\n"
        f"🎬 Ko'rilgan animelar: {watched} ta",
        parse_mode="HTML",
    )


@router.message(F.text == "🕘 Tarix")
async def btn_history(message: Message):
    rows = db("""SELECT a.title, h.anime_code, h.season_number, MAX(h.episode_number), MAX(h.watched_date)
                 FROM history h JOIN animes a ON a.code = h.anime_code
                 WHERE h.user_id=? GROUP BY h.anime_code, h.season_number ORDER BY MAX(h.watched_date) DESC""",
              (message.from_user.id,), "all")
    if not rows:
        await message.answer("🕘 Tarixingiz hozircha bo'sh.")
        return
    text = "🕘 <b>Tomosha tarixi</b>\n\n"
    for title, code, season, last_ep, _ in rows[:20]:
        text += f"• {title} — {season}-fasl, {last_ep}-qismgacha (kod: {code})\n"
    await message.answer(text, parse_mode="HTML")


# ============ ADMIN: ANIME QO'SHISH (poster botga to'g'ridan-to'g'ri) ============
@router.message(Command("addanime"))
async def cmd_addanime(message: Message):
    if message.from_user.id not in ADMIN_IDS:
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
        "Eslatma: agar anime bir nechta fasldan iborat bo'lsa, hammasi shu bitta anime kodi ostida "
        "birlashadi — \"Fasllar\" qatoriga jami fasl sonini yozing.",
        parse_mode="HTML",
    )


@router.message(F.photo, F.caption.contains("Kod:"))
async def process_addanime(message: Message):
    if message.from_user.id not in ADMIN_IDS:
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

    poster_file_id = message.photo[-1].file_id

    db("""INSERT OR REPLACE INTO animes
          (code, title, total_seasons, quality, genre, rating,
           views, is_premium, poster_file_id, added_date)
          VALUES (?,?,?,?,?,?,COALESCE((SELECT views FROM animes WHERE code=?),0),?,?,?)""",
       (code, title, total_seasons, quality, genre, rating, code, is_premium, poster_file_id,
        datetime.now().isoformat()))

    await message.answer(
        f"✅ Anime qo'shildi!\nKod: <code>{code}</code>\n\n"
        f"Endi qismlarini qo'shish uchun videoni to'g'ridan-to'g'ri botga yuboring, "
        f"caption qismiga yozing:\n<code>/addep {code} 1 1</code>\n"
        f"(kod, fasl raqami, qism raqami)",
        parse_mode="HTML",
    )

    # Kanalga avtomatik e'lon qilish (agar sozlangan bo'lsa)
    if ANNOUNCE_CHANNEL_ID and BOT_USERNAME:
        announce_caption = (
            f"★ {title}\n"
            f"★ Fasllar: {total_seasons}\n"
            f"★ Sifat: {quality} | Janr: {genre}\n"
            f"★ Reyting: {rating}"
        )
        watch_kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◁ Tomosha qilish ▷", url=f"https://t.me/{BOT_USERNAME}?start={code}")
        ]])
        try:
            await bot.send_photo(int(ANNOUNCE_CHANNEL_ID), poster_file_id,
                                  caption=announce_caption, reply_markup=watch_kb)
        except TelegramBadRequest as e:
            logging.error(f"Announce error: {e}")
            await message.answer("⚠️ Anime qo'shildi, lekin kanalga e'lon qilishda xatolik (bot kanalga admin ekanini tekshiring).")


# ============ ADMIN: QISM QO'SHISH (video to'g'ridan-to'g'ri botga) ============
@router.message(F.video, F.caption.startswith("/addep"))
async def process_addep(message: Message):
    if message.from_user.id not in ADMIN_IDS:
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
        await message.answer("❌ Bunday kodli anime topilmadi, avval /addanime bilan qo'shing.")
        return

    file_id = message.video.file_id
    db("INSERT OR REPLACE INTO episodes (anime_code, season_number, episode_number, file_id) VALUES (?,?,?,?)",
       (code, season, num, file_id))
    await message.answer(f"✅ {code}-anime, {season}-fasl, {num}-qism qo'shildi!")


@router.message(Command("addep"))
async def cmd_addep_reply(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        return
    # Usul 2: video (boshqa botdan/joydan forward qilingan bo'lsa ham) xabariga JAVOBAN /addep yozilsa
    if message.reply_to_message and message.reply_to_message.video and command.args:
        parts = command.args.split()
        if len(parts) != 3:
            await message.answer("❗️ Format: /addep KOD FASL QISM  (masalan: /addep 25 1 1)")
            return
        code, season, num = parts
        try:
            season, num = int(season), int(num)
        except ValueError:
            await message.answer("❗️ Fasl va qism raqam bo'lishi kerak.")
            return
        if not db("SELECT 1 FROM animes WHERE code=?", (code,), "one"):
            await message.answer("❌ Bunday kodli anime topilmadi, avval /addanime bilan qo'shing.")
            return
        file_id = message.reply_to_message.video.file_id
        db("INSERT OR REPLACE INTO episodes (anime_code, season_number, episode_number, file_id) VALUES (?,?,?,?)",
           (code, season, num, file_id))
        await message.answer(f"✅ {code}-anime, {season}-fasl, {num}-qism qo'shildi!")
        return

    await message.answer(
        "❗️ Qism qo'shishning 2 usuli bor:\n\n"
        "1) Videoni to'g'ridan-to'g'ri botga yuboring, caption qismiga yozing:\n"
        "<code>/addep KOD FASL QISM</code>\n\n"
        "2) Videoni (boshqa botdan/kanaldan) botga <b>forward</b> qiling, "
        "so'ng o'sha forward qilingan xabarga <b>javob (reply)</b> tariqasida yozing:\n"
        "<code>/addep KOD FASL QISM</code>",
        parse_mode="HTML",
    )


# ============ ADMIN: ANIME O'CHIRISH ============
@router.message(Command("delanime"))
async def cmd_delanime(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not command.args:
        await message.answer("❗️ Foydalanish: /delanime KOD\nMasalan: /delanime 25")
        return
    code = command.args.strip()
    if not db("SELECT 1 FROM animes WHERE code=?", (code,), "one"):
        await message.answer("❌ Bunday kodli anime topilmadi.")
        return
    db("DELETE FROM animes WHERE code=?", (code,))
    db("DELETE FROM episodes WHERE anime_code=?", (code,))
    db("DELETE FROM history WHERE anime_code=?", (code,))
    await message.answer(f"🗑 {code}-anime va uning barcha qismlari o'chirildi.")


# ============ ADMIN: BITTA QISMNI O'CHIRISH ============
@router.message(Command("delep"))
async def cmd_delep(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not command.args:
        await message.answer("❗️ Foydalanish: /delep KOD FASL QISM\nMasalan: /delep 25 1 3")
        return
    try:
        code, season, num = command.args.split()
        season, num = int(season), int(num)
    except ValueError:
        await message.answer("❗️ Foydalanish: /delep KOD FASL QISM")
        return
    db("DELETE FROM episodes WHERE anime_code=? AND season_number=? AND episode_number=?",
       (code, season, num))
    await message.answer(f"🗑 {code}-anime, {season}-fasl, {num}-qism o'chirildi.")


# ============ ADMIN: VIP BERISH ============
MONTH_TO_DAYS = {"1oy": 30, "2oy": 60, "3oy": 90, "6oy": 180, "8oy": 240}


@router.message(Command("givevip"))
async def cmd_givevip(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not command.args:
        await message.answer(
            "❗️ Foydalanish: /givevip USER_ID MUDDAT\n"
            "Muddat variantlari: 1oy, 2oy, 3oy, 6oy, 8oy yoki 'forever'\n"
            "Masalan: /givevip 123456789 3oy"
        )
        return
    try:
        parts = command.args.split()
        target_id = int(parts[0])
        duration = parts[1]
    except (ValueError, IndexError):
        await message.answer("❗️ Foydalanish: /givevip USER_ID MUDDAT (masalan: /givevip 123456789 3oy)")
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


# ============ ADMIN: XABAR YUBORISH ============
@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
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


# ============ ADMIN: QISQA STATISTIKA ============
@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    animes_count = db("SELECT COUNT(*) FROM animes", (), "one")[0]
    users_count = db("SELECT COUNT(*) FROM users", (), "one")[0]
    vip_count = db("SELECT COUNT(*) FROM users WHERE is_vip=1", (), "one")[0]
    await message.answer(
        f"📊 Statistika:\n🎬 Animelar: {animes_count}\n👥 Foydalanuvchilar: {users_count}\n💎 VIP: {vip_count}"
    )


# ============ KEEP-ALIVE WEB SERVER (Render/UptimeRobot uchun) ============
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
    await start_webserver()
    print("Anime bot ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
