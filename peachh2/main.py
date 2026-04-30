import telebot
import random
import time
import os
import threading
from telebot import types
from flask import Flask
from pymongo import MongoClient

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER ---
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running and connected to DB..."

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# --- НАСТРОЙКИ ---
TOKEN = '8504693959:AAEJ2yKNbSKBmRbkohihsZM28OuLRmuGz_I'
ADMIN_ID = 8301845376

bot = telebot.TeleBot(TOKEN)

# --- ПОДКЛЮЧЕНИЕ К БАЗЕ ДАННЫХ MONGODB ---
# Убедись, что добавил переменную MONGO_URI в настройках Render Environment!
MONGO_URI = os.environ.get("MONGO_URI", "ВСТАВЬ_СЮДА_ССЫЛКУ_НА_MONGODB_ЕСЛИ_НЕ_ИСПОЛЬЗУЕШЬ_ENV")
client = MongoClient(MONGO_URI)
db = client['telegram_bot_db']

users_col = db['users']
media_col = db['media']
coupons_col = db['coupons']
counters_col = db['counters']

# Временные переменные (в памяти)
user_states = {}
anti_spam = {} 
admin_media_groups = set()

# --- ФУНКЦИИ БАЗЫ ДАННЫХ ---
def get_user(user_id):
    """Получает юзера из БД или создает нового."""
    uid_str = str(user_id)
    user = users_col.find_one({"_id": uid_str})
    if not user:
        user = {"_id": uid_str, "balance": 3, "used_coupons": [], "banned": False}
        users_col.insert_one(user)
    return user

def get_next_media_id():
    """Генерирует следующий порядковый номер для медиафайла."""
    doc = counters_col.find_one_and_update(
        {"_id": "media_id"},
        {"$inc": {"sequence_value": 1}},
        upsert=True,
        return_document=True
    )
    return str(doc["sequence_value"])

# --- АНТИ-СПАМ ---
def is_spamming(message_or_call):
    user_id = message_or_call.from_user.id
    if user_id == ADMIN_ID: return False 
    
    # Проверка постоянного бана из БД
    user = get_user(user_id)
    if user.get("banned"):
        return True
    
    now = time.time()
    if user_id not in anti_spam:
        anti_spam[user_id] = {"times": [], "warnings": 0, "muted_until": 0}
    
    tracker = anti_spam[user_id]
    if now < tracker["muted_until"]: return True

    tracker["times"] = [t for t in tracker["times"] if now - t <= 7]
    tracker["times"].append(now)
    
    if len(tracker["times"]) >= 5:
        tracker["times"] = [] 
        chat_id = message_or_call.message.chat.id if hasattr(message_or_call, 'message') else message_or_call.chat.id
        
        if tracker["warnings"] == 0:
            tracker["warnings"] = 1
            bot.send_message(chat_id, "⚠️ Предупреждение: Не спамь! Подожди немного.")
        elif tracker["warnings"] == 1:
            tracker["warnings"] = 2
            tracker["muted_until"] = now + 300
            bot.send_message(chat_id, "🔇 Вы заглушены на 5 минут за спам.")
        else:
            # Выдаем вечный бан и пишем в БД
            users_col.update_one({"_id": str(user_id)}, {"$set": {"banned": True}})
            username = f"@{message_or_call.from_user.username}" if message_or_call.from_user.username else "No Name"
            bot.send_message(chat_id, "⛔ Вы навсегда заблокированы за спам.")
            bot.send_message(ADMIN_ID, f"🚨 ЮЗЕР ЗАБАНЕН ЗА СПАМ: {username} ({user_id})")
        return True
    return False

# --- КЛАВИАТУРЫ ---
def get_user_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("Get Photo 📷 (1 🪙)", "Get Video 🎥")
    markup.add("My Balance 💰", "Buy Coins 🪙")
    markup.add("Enter Promo Code 🎫")
    return markup

def get_admin_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("Add Media ➕", "Manage Media ⚙️")
    markup.add("Create Coupon 🎫", "Unban User 🔓")
    markup.add("Statistics 📊", "Exit Admin Mode ❌")
    return markup

def get_shop_markup():
    markup = types.InlineKeyboardMarkup(row_width=1)
    prices = {"10": 8, "50": 40, "100": 80, "150": 100, "200": 160}
    for c, s in prices.items():
        markup.add(types.InlineKeyboardButton(text=f"{c} Coins for {s} ⭐️", callback_data=f"pay_{c}"))
    return markup

# --- АДМИН-ПАНЕЛЬ ---
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id == ADMIN_ID:
        bot.send_message(message.chat.id, "Admin mode activated.", reply_markup=get_admin_markup())
    else:
        bot.send_message(message.chat.id, "No access.")

@bot.message_handler(func=lambda m: m.text == "Create Coupon 🎫" and m.from_user.id == ADMIN_ID)
def admin_coupon_start(message):
    user_states[ADMIN_ID] = {"state": "wait_coupon_name"}
    bot.send_message(ADMIN_ID, "Введи название промокода (например, CREAM):")

@bot.message_handler(func=lambda m: m.text == "Unban User 🔓" and m.from_user.id == ADMIN_ID)
def admin_unban_start(message):
    user_states[ADMIN_ID] = {"state": "wait_unban_id"}
    bot.send_message(ADMIN_ID, "Введи ID юзера для разбана:")

@bot.message_handler(func=lambda m: m.text == "Manage Media ⚙️" and m.from_user.id == ADMIN_ID)
def manage_media_start(message):
    user_states[ADMIN_ID] = {"state": "waiting_for_admin_id"}
    bot.send_message(ADMIN_ID, "Введи ID медиа для просмотра/удаления:")

@bot.message_handler(func=lambda m: m.text == "Statistics 📊" and m.from_user.id == ADMIN_ID)
def show_stats(message):
    users_count = users_col.count_documents({})
    photos_count = media_col.count_documents({"type": "photo"})
    videos_count = media_col.count_documents({"type": "video"})
    bot.send_message(ADMIN_ID, f"📊 Статистика:\n👥 Пользователей: {users_count}\n📷 Фото: {photos_count}\n🎥 Видео: {videos_count}")

@bot.message_handler(func=lambda m: m.text == "Exit Admin Mode ❌" and m.from_user.id == ADMIN_ID)
def exit_admin(message):
    bot.send_message(message.chat.id, "Exited Admin Mode.", reply_markup=get_user_markup())

# --- ОБРАБОТЧИК СОСТОЯНИЙ (ВВОД ДАННЫХ) ---
@bot.message_handler(func=lambda m: m.from_user.id in user_states)
def handle_states(message):
    uid = message.from_user.id
    state_data = user_states[uid]
    state = state_data["state"]

    if uid == ADMIN_ID:
        if state == "wait_coupon_name":
            user_states[uid] = {"state": "wait_coupon_value", "name": message.text.strip().upper()}
            bot.send_message(ADMIN_ID, f"Промокод '{message.text.upper()}' создан. Введи сумму монет:")
        elif state == "wait_coupon_value":
            if message.text.isdigit():
                name = state_data["name"]
                amount = int(message.text)
                coupons_col.update_one({"_id": name}, {"$set": {"amount": amount}}, upsert=True)
                bot.send_message(ADMIN_ID, f"✅ Успешно! Промокод '{name}' дает {amount} монет.", reply_markup=get_admin_markup())
                del user_states[uid]
            else:
                bot.send_message(ADMIN_ID, "Пожалуйста, введи число.")
        
        elif state == "wait_unban_id":
            target_id = message.text.strip()
            if target_id.isdigit():
                res = users_col.update_one({"_id": target_id}, {"$set": {"banned": False}})
                if int(target_id) in anti_spam: del anti_spam[int(target_id)]
                if res.modified_count > 0:
                    bot.send_message(ADMIN_ID, f"✅ Юзер {target_id} успешно разбанен.", reply_markup=get_admin_markup())
                else:
                    bot.send_message(ADMIN_ID, "❌ Юзер не найден или не был в бане.", reply_markup=get_admin_markup())
                del user_states[uid]
            else:
                bot.send_message(ADMIN_ID, "Пожалуйста, введи числовой ID.")

        elif state == "waiting_for_admin_id":
            mid = message.text.strip()
            media = media_col.find_one({"_id": mid})
            if media:
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Delete Media 🗑", callback_data=f"delete_{mid}"))
                if media["type"] == "photo":
                    bot.send_photo(ADMIN_ID, media["file_id"], caption=f"ID: {mid}", reply_markup=markup)
                else:
                    bot.send_video(ADMIN_ID, media["file_id"], caption=f"ID: {mid}", reply_markup=markup)
            else: 
                bot.send_message(ADMIN_ID, "❌ Файл с таким ID не найден.")
            del user_states[uid]

    else: # ПОЛЬЗОВАТЕЛИ
        if is_spamming(message): return
        if state == "wait_promo_input":
            code = message.text.strip().upper()
            user = get_user(uid)
            
            coupon = coupons_col.find_one({"_id": code})
            if coupon:
                if code in user.get("used_coupons", []):
                    bot.send_message(message.chat.id, "❌ Ты уже использовал этот промокод.")
                else:
                    amount = coupon["amount"]
                    users_col.update_one({"_id": str(uid)}, {"$inc": {"balance": amount}, "$push": {"used_coupons": code}})
                    bot.send_message(message.chat.id, f"✅ Успех! Начислено {amount} 🪙.")
            else:
                bot.send_message(message.chat.id, "❌ Неверный промокод.")
            del user_states[uid]
            
        elif state == "waiting_for_report":
            username = f"@{message.from_user.username}" if message.from_user.username else "No Name"
            bot.send_message(ADMIN_ID, f"🚩 ЖАЛОБА\nЮзер: {username} ({uid})\nМедиа ID: {state_data['media_id']}\nПричина: {message.text}")
            bot.send_message(message.chat.id, "Жалоба отправлена администратору. Спасибо!")
            del user_states[uid]

# --- КНОПКИ ПОЛЬЗОВАТЕЛЕЙ ---
@bot.message_handler(func=lambda m: m.text == "Enter Promo Code 🎫")
def user_promo_start(message):
    if is_spamming(message): return
    user_states[message.from_user.id] = {"state": "wait_promo_input"}
    bot.send_message(message.chat.id, "Введи промокод:")

@bot.message_handler(func=lambda m: m.text == "My Balance 💰")
def balance(message):
    if is_spamming(message): return
    user = get_user(message.from_user.id)
    bot.send_message(message.chat.id, f"Твой баланс: {user['balance']} 🪙")

@bot.message_handler(commands=['start'])
def start(message):
    if is_spamming(message): return
    get_user(message.from_user.id) # Создаем юзера в БД, если его нет
    bot.send_message(message.chat.id, "Добро пожаловать! Выбери опцию:", reply_markup=get_user_markup())

@bot.message_handler(func=lambda m: m.text in ["Get Photo 📷 (1 🪙)", "Get Video 🎥", "Buy Coins 🪙"])
def fast_menu(message):
    if is_spamming(message): return
    uid = message.from_user.id
    user = get_user(uid)
    
    if "Photo" in message.text:
        if uid != ADMIN_ID and user['balance'] < 1:
            return bot.send_message(message.chat.id, "❌ Недостаточно монет!")
        
        # Получаем 1 случайное фото из БД (самый быстрый метод для MongoDB)
        random_photos = list(media_col.aggregate([{"$match": {"type": "photo"}}, {"$sample": {"size": 1}}]))
        if not random_photos: 
            return bot.send_message(message.chat.id, "❌ База фото пока пуста.")
        
        if uid != ADMIN_ID:
            users_col.update_one({"_id": str(uid)}, {"$inc": {"balance": -1}})
            
        media = random_photos[0]
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Report 🚩", callback_data=f"report_{media['_id']}"))
        bot.send_photo(message.chat.id, media["file_id"], caption=f"ID: {media['_id']}", reply_markup=markup)
    
    elif "Video" in message.text:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("0-10s (2 🪙)", callback_data="buy_vid_10"),
                   types.InlineKeyboardButton("11-30s (3 🪙)", callback_data="buy_vid_30"),
                   types.InlineKeyboardButton("31s+ (5 🪙)", callback_data="buy_vid_all"))
        bot.send_message(message.chat.id, "Выбери длительность:", reply_markup=markup)
    
    elif "Buy" in message.text:
        bot.send_message(message.chat.id, "Магазин монет:", reply_markup=get_shop_markup())

# --- ДОБАВЛЕНИЕ МЕДИА (АДМИН) ---
@bot.message_handler(content_types=['photo', 'video'])
def handle_upload(message):
    if message.from_user.id != ADMIN_ID: return
    
    new_id = get_next_media_id()
    
    if message.content_type == 'photo':
        # Telegram отправляет фото в разных размерах, берем самое качественное [-1]
        media_col.insert_one({"_id": new_id, "file_id": message.photo[-1].file_id, "type": "photo", "category": None})
    else:
        duration = message.video.duration
        cat = "10" if duration <= 10 else "30" if duration <= 30 else "all"
        media_col.insert_one({"_id": new_id, "file_id": message.video.file_id, "type": "video", "category": cat})
    
    # Логика, чтобы не спамить уведомлениями при загрузке альбома
    if message.media_group_id:
        if message.media_group_id not in admin_media_groups:
            admin_media_groups.add(message.media_group_id)
            bot.send_message(ADMIN_ID, f"✅ Альбом загружен в базу! (Файлам присвоены ID)")
            # Очистка памяти сета, чтобы не копилось бесконечно
            if len(admin_media_groups) > 100: admin_media_groups.clear() 
    else:
        bot.send_message(ADMIN_ID, f"✅ Файл добавлен! (ID: {new_id})")

# --- ОБРАБОТКА ИНЛАЙН КНОПОК ---
@bot.callback_query_handler(func=lambda call: True)
def handle_calls(call):
    if is_spamming(call): return
    uid_str = str(call.from_user.id)
    
    if call.data.startswith("pay_"):
        amt = call.data.split("_")[1]
        p = {"10": 8, "50": 40, "100": 80, "150": 100, "200": 160}
        bot.send_invoice(call.message.chat.id, "Монеты", f"Купить {amt} 🪙", f"coins_{amt}", "", "XTR", [types.LabeledPrice("XTR", p[amt])])
        
    elif call.data.startswith("buy_vid_"): # ДОБАВЛЕНО: Выдача видео после нажатия на цену
        cat = call.data.split("_")[2]
        prices = {"10": 2, "30": 3, "all": 5}
        price = prices[cat]
        user = get_user(call.from_user.id)
        
        if call.from_user.id != ADMIN_ID and user["balance"] < price:
            bot.answer_callback_query(call.id, "❌ Недостаточно монет!", show_alert=True)
            return
            
        random_videos = list(media_col.aggregate([{"$match": {"type": "video", "category": cat}}, {"$sample": {"size": 1}}]))
        if not random_videos:
            bot.answer_callback_query(call.id, "❌ Нет видео в этой категории.", show_alert=True)
            return
            
        if call.from_user.id != ADMIN_ID:
            users_col.update_one({"_id": uid_str}, {"$inc": {"balance": -price}})
            
        media = random_videos[0]
        markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Report 🚩", callback_data=f"report_{media['_id']}"))
        bot.send_video(call.message.chat.id, media["file_id"], caption=f"ID: {media['_id']}", reply_markup=markup)
        bot.answer_callback_query(call.id)

    elif call.data.startswith("report_"):
        user_states[call.from_user.id] = {"state": "waiting_for_report", "media_id": call.data.split("_")[1]}
        bot.send_message(call.message.chat.id, "Напиши причину жалобы текстом:")
        bot.answer_callback_query(call.id)
        
    elif call.data.startswith("delete_") and call.from_user.id == ADMIN_ID:
        mid = call.data.split("_")[1]
        media_col.delete_one({"_id": mid})
        try:
            bot.edit_message_caption(f"❌ ID: {mid} - УДАЛЕНО", call.message.chat.id, call.message.message_id)
        except Exception: pass
        bot.answer_callback_query(call.id, "Удалено из БД!")

@bot.pre_checkout_query_handler(func=lambda q: True)
def checkout(q): 
    bot.answer_pre_checkout_query(q.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def pay_ok(message):
    amt = int(message.successful_payment.invoice_payload.split("_")[1])
    users_col.update_one({"_id": str(message.from_user.id)}, {"$inc": {"balance": amt}}, upsert=True)
    bot.send_message(message.chat.id, f"✅ Баланс пополнен на {amt} 🪙!")

# --- ЗАПУСК ---
if __name__ == '__main__':
    threading.Thread(target=run_web_server, daemon=True).start()
    print("Бот запускается... База подключена.")
    # Используем infinity_polling для обхода возможных ошибок соединения Telegram
    bot.infinity_polling(timeout=20, long_polling_timeout=15)
