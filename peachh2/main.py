import telebot
import random
import json
import time
import os
import threading
from telebot import types
from flask import Flask

# --- WEB SERVER FOR RENDER ---
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running..."

def run_web_server():
    # Render передает порт в переменную окружения PORT
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# --- SETTINGS ---
TOKEN = '8504693959:AAEJ2yKNbSKBmRbkohihsZM28OuLRmuGz_I'
ADMIN_ID = 8301845376

bot = telebot.TeleBot(TOKEN)

user_states = {}
anti_spam = {} 
admin_media_groups = {}

# --- DATABASE HELPERS ---
def load_data(filename, default_val):
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f: 
                return json.load(f)
        return default_val
    except (FileNotFoundError, json.JSONDecodeError):
        return default_val

def save_data(filename, data):
    with open(filename, 'w', encoding='utf-8') as f: 
        json.dump(data, f, ensure_ascii=False, indent=4)

# Initialize Databases
media_db = load_data('media.json', {"counter": 0, "files": {}, "photos": [], "videos": {"10": [], "30": [], "all": []}})
users_db = load_data('users.json', {})
coupons_db = load_data('coupons.json', {})

# --- ANTI-SPAM LOGIC ---
def is_spamming(obj):
    user_id = obj.from_user.id
    if user_id == ADMIN_ID: return False 
    
    now = time.time()
    if user_id not in anti_spam:
        anti_spam[user_id] = {"times": [], "warnings": 0, "muted_until": 0, "banned": False}
    
    tracker = anti_spam[user_id]
    if tracker["banned"] or now < tracker["muted_until"]: return True

    tracker["times"] = [t for t in tracker["times"] if now - t <= 7]
    tracker["times"].append(now)
    
    if len(tracker["times"]) >= 5:
        tracker["times"] = [] 
        chat_id = obj.message.chat.id if hasattr(obj, 'message') else obj.chat.id
        if tracker["warnings"] == 0:
            tracker["warnings"] = 1
            bot.send_message(chat_id, "⚠️ Warning: Stop spamming! Slow down.")
        elif tracker["warnings"] == 1:
            tracker["warnings"] = 2
            tracker["muted_until"] = now + 300
            bot.send_message(chat_id, "🔇 You are muted for 5 minutes.")
        else:
            tracker["banned"] = True
            username = f"@{obj.from_user.username}" if obj.from_user.username else "No Name"
            bot.send_message(chat_id, "⛔ You are permanently banned.")
            bot.send_message(ADMIN_ID, f"🚨 USER BANNED: {username} ({user_id})")
        return True
    return False

# --- KEYBOARDS ---
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

# --- ADMIN COMMANDS ---
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id == ADMIN_ID:
        bot.send_message(message.chat.id, "Admin mode activated.", reply_markup=get_admin_markup())
    else:
        bot.send_message(message.chat.id, "No access.")

@bot.message_handler(func=lambda m: m.text == "Create Coupon 🎫" and m.from_user.id == ADMIN_ID)
def admin_coupon_start(message):
    user_states[ADMIN_ID] = {"state": "wait_coupon_name"}
    bot.send_message(ADMIN_ID, "Enter the Name for the promo code (e.g., CREAM):")

@bot.message_handler(func=lambda m: m.text == "Unban User 🔓" and m.from_user.id == ADMIN_ID)
def admin_unban_start(message):
    user_states[ADMIN_ID] = {"state": "wait_unban_id"}
    bot.send_message(ADMIN_ID, "Enter the User ID you want to unban:")

@bot.message_handler(func=lambda m: m.text == "Manage Media ⚙️" and m.from_user.id == ADMIN_ID)
def manage_media_start(message):
    user_states[ADMIN_ID] = {"state": "waiting_for_admin_id"}
    bot.send_message(ADMIN_ID, "Enter Media ID to view/delete:")

@bot.message_handler(func=lambda m: m.text == "Exit Admin Mode ❌" and m.from_user.id == ADMIN_ID)
def exit_admin(message):
    bot.send_message(message.chat.id, "Exited Admin Mode.", reply_markup=get_user_markup())

# --- STATE HANDLER ---
@bot.message_handler(func=lambda m: m.from_user.id in user_states)
def handle_states(message):
    uid = message.from_user.id
    state_data = user_states[uid]
    state = state_data["state"]

    if uid == ADMIN_ID:
        if state == "wait_coupon_name":
            user_states[uid] = {"state": "wait_coupon_value", "name": message.text.strip().upper()}
            bot.send_message(ADMIN_ID, f"Coupon '{message.text}' created. Now enter coin amount:")
        elif state == "wait_coupon_value":
            if message.text.isdigit():
                name = state_data["name"]
                amount = int(message.text)
                coupons_db[name] = amount
                save_data('coupons.json', coupons_db)
                bot.send_message(ADMIN_ID, f"✅ Success! Code '{name}' now gives {amount} coins.", reply_markup=get_admin_markup())
                del user_states[uid]
            else:
                bot.send_message(ADMIN_ID, "Please enter a valid number.")
        
        elif state == "wait_unban_id":
            target_id = message.text.strip()
            if target_id.isdigit():
                t_id_int = int(target_id)
                if t_id_int in anti_spam:
                    anti_spam[t_id_int]["banned"] = False
                    anti_spam[t_id_int]["warnings"] = 0
                    anti_spam[t_id_int]["muted_until"] = 0
                    bot.send_message(ADMIN_ID, f"✅ User {target_id} has been unbanned.", reply_markup=get_admin_markup())
                else:
                    bot.send_message(ADMIN_ID, "❌ User ID not found.", reply_markup=get_admin_markup())
                del user_states[uid]
            else:
                bot.send_message(ADMIN_ID, "Please enter a numeric User ID.")

        elif state == "waiting_for_admin_id":
            mid = message.text.strip()
            if mid in media_db["files"]:
                f = media_db["files"][mid]
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Delete Media 🗑", callback_data=f"delete_{mid}"))
                if f["type"] == "photo": bot.send_photo(ADMIN_ID, f["file_id"], caption=f"ID: {mid}", reply_markup=markup)
                else: bot.send_video(ADMIN_ID, f["file_id"], caption=f"ID: {mid}", reply_markup=markup)
            else: bot.send_message(ADMIN_ID, "ID not found.")
            del user_states[uid]

    else:
        if is_spamming(message): return
        if state == "wait_promo_input":
            code = message.text.strip().upper()
            u_str = str(uid)
            if u_str not in users_db: users_db[u_str] = {"balance": 3, "used_coupons": []}
            
            if code in coupons_db:
                if code in users_db[u_str].get("used_coupons", []):
                    bot.send_message(message.chat.id, "❌ You have already used this code.")
                else:
                    amount = coupons_db[code]
                    users_db[u_str]["balance"] += amount
                    if "used_coupons" not in users_db[u_str]: users_db[u_str]["used_coupons"] = []
                    users_db[u_str]["used_coupons"].append(code)
                    save_data('users.json', users_db)
                    bot.send_message(message.chat.id, f"✅ Success! You received {amount} 🪙.")
            else:
                bot.send_message(message.chat.id, "❌ Invalid promo code.")
            del user_states[uid]
            
        elif state == "waiting_for_report":
            username = f"@{message.from_user.username}" if message.from_user.username else "No Name"
            bot.send_message(ADMIN_ID, f"🚩 REPORT\nUser: {username}\nMedia: {state_data['media_id']}\nReason: {message.text}")
            bot.send_message(message.chat.id, "Report sent to admin.")
            del user_states[uid]

# --- USER BUTTONS ---
@bot.message_handler(func=lambda m: m.text == "Enter Promo Code 🎫")
def user_promo_start(message):
    if is_spamming(message): return
    user_states[message.from_user.id] = {"state": "wait_promo_input"}
    bot.send_message(message.chat.id, "Please type your promo code:")

@bot.message_handler(func=lambda m: m.text == "My Balance 💰")
def balance(message):
    if is_spamming(message): return
    uid = str(message.from_user.id)
    if uid not in users_db: users_db[uid] = {"balance": 3, "used_coupons": []}
    bot.send_message(message.chat.id, f"Your balance: {users_db[uid]['balance']} 🪙")

@bot.message_handler(commands=['start'])
def start(message):
    if is_spamming(message): return
    bot.send_message(message.chat.id, "Welcome! Select an option:", reply_markup=get_user_markup())

@bot.message_handler(func=lambda m: m.text in ["Get Photo 📷 (1 🪙)", "Get Video 🎥", "Buy Coins 🪙"])
def fast_menu(message):
    if is_spamming(message): return
    uid = str(message.from_user.id)
    if uid not in users_db: users_db[uid] = {"balance": 3, "used_coupons": []}
    
    if "Photo" in message.text:
        if message.from_user.id != ADMIN_ID and users_db[uid]['balance'] < 1:
            return bot.send_message(message.chat.id, "Not enough coins!")
        if not media_db["photos"]: return bot.send_message(message.chat.id, "DB is empty.")
        if message.from_user.id != ADMIN_ID: users_db[uid]['balance'] -= 1
        save_data('users.json', users_db)
        mid = random.choice(media_db["photos"])
        bot.send_photo(message.chat.id, media_db["files"][mid]["file_id"], caption=f"ID: {mid}", reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Report 🚩", callback_data=f"report_{mid}")))
    
    elif "Video" in message.text:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("0-10s (2 🪙)", callback_data="buy_vid_10"),
                   types.InlineKeyboardButton("11-30s (3 🪙)", callback_data="buy_vid_30"),
                   types.InlineKeyboardButton("31s+ (5 🪙)", callback_data="buy_vid_all"))
        bot.send_message(message.chat.id, "Select duration:", reply_markup=markup)
    
    elif "Buy" in message.text:
        bot.send_message(message.chat.id, "Shop:", reply_markup=get_shop_markup())

@bot.message_handler(content_types=['photo', 'video'])
def handle_upload(message):
    if message.from_user.id != ADMIN_ID: return
    media_db["counter"] += 1
    new_id = str(media_db["counter"])
    if message.content_type == 'photo':
        media_db["files"][new_id] = {"file_id": message.photo[-1].file_id, "type": "photo", "category": None}
        media_db["photos"].append(new_id)
    else:
        cat = "10" if message.video.duration <= 10 else "30" if message.video.duration <= 30 else "all"
        media_db["files"][new_id] = {"file_id": message.video.file_id, "type": "video", "category": cat}
        media_db["videos"][cat].append(new_id)
    save_data('media.json', media_db)
    if not message.media_group_id or message.media_group_id not in admin_media_groups:
        if message.media_group_id: admin_media_groups[message.media_group_id] = True
        bot.send_message(ADMIN_ID, f"✅ Media added! (Last ID: {new_id})")

@bot.callback_query_handler(func=lambda call: True)
def handle_calls(call):
    if is_spamming(call): return
    uid = str(call.from_user.id)
    if call.data.startswith("pay_"):
        amt = call.data.split("_")[1]
        p = {"10": 8, "50": 40, "100": 80, "150": 100, "200": 160}
        bot.send_invoice(call.message.chat.id, "Coins", f"Buy {amt} 🪙", f"coins_{amt}", "", "XTR", [types.LabeledPrice("XTR", p[amt])])
    elif call.data.startswith("report_"):
        user_states[call.from_user.id] = {"state": "waiting_for_report", "media_id": call.data.split("_")[1]}
        bot.send_message(call.message.chat.id, "Type reason for report:")
    elif call.data.startswith("delete_") and call.from_user.id == ADMIN_ID:
        mid = call.data.split("_")[1]
        if mid in media_db["files"]:
            f = media_db["files"][mid]
            if f["type"] == "photo": media_db["photos"].remove(mid)
            else: media_db["videos"][f["category"]].remove(mid)
            del media_db["files"][mid]
            save_data('media.json', media_db)
            bot.edit_message_caption("❌ Deleted", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.pre_checkout_query_handler(func=lambda q: True)
def checkout(q): bot.answer_pre_checkout_query(q.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def pay_ok(message):
    amt = int(message.successful_payment.invoice_payload.split("_")[1])
    uid = str(message.from_user.id)
    if uid not in users_db: users_db[uid] = {"balance": 0}
    users_db[uid]["balance"] += amt
    save_data('users.json', users_db)
    bot.send_message(message.chat.id, f"✅ Added {amt} 🪙!")

# --- MAIN RUN BLOCK ---
if __name__ == '__main__':
    # Запускаем веб-сервер в отдельном потоке
    threading.Thread(target=run_web_server, daemon=True).start()
    # Запускаем бота
    bot.infinity_polling(timeout=10, long_polling_timeout=5)