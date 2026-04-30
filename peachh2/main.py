import telebot
import time
import os
import threading
from telebot import types
from flask import Flask
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

# --- WEB SERVER FOR RENDER ---
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot status: Active. Database: Connected."

def run_web_server():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# --- SETTINGS ---
TOKEN = '8504693959:AAEJ2yKNbSKBmRbkohihsZM28OuLRmuGz_I'
# MAKE SURE THIS MATCHES YOUR ACTUAL TELEGRAM ID
ADMIN_ID = 8301845376

bot = telebot.TeleBot(TOKEN)

# --- DATABASE CONNECTION ---
MONGO_URI = os.environ.get("MONGO_URI")
if not MONGO_URI:
    print("CRITICAL ERROR: MONGO_URI environment variable is not set!")

# 5-second timeout to prevent the bot from freezing if the DB is unreachable
try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.server_info() # Test connection
    print("Database connected successfully.")
except ServerSelectionTimeoutError:
    print("CRITICAL ERROR: Could not connect to MongoDB. Check IP Whitelist in Atlas!")

db = client['telegram_bot_db']
users_col = db['users']
media_col = db['media']
coupons_col = db['coupons']
counters_col = db['counters']

# Temporary memory storage
user_states = {}
anti_spam = {} 
admin_media_groups = set()

# --- DATABASE HELPERS ---
def get_user(user_id):
    uid_str = str(user_id)
    user = users_col.find_one({"_id": uid_str})
    if not user:
        user = {"_id": uid_str, "balance": 3, "used_coupons": [], "banned": False}
        users_col.insert_one(user)
    return user

def get_next_media_id():
    doc = counters_col.find_one_and_update(
        {"_id": "media_id"},
        {"$inc": {"sequence_value": 1}},
        upsert=True,
        return_document=True
    )
    return str(doc.get("sequence_value", 1))

# --- ANTI-SPAM ---
def is_spamming(message_or_call):
    user_id = message_or_call.from_user.id
    if user_id == ADMIN_ID: return False 
    
    user = get_user(user_id)
    if user.get("banned"): return True
    
    now = time.time()
    if user_id not in anti_spam:
        anti_spam[user_id] = {"times": [], "warnings": 0, "muted_until": 0}
    
    tracker = anti_spam[user_id]
    if now < tracker["muted_until"]: return True

    tracker["times"] = [t for t in tracker["times"] if now - t <= 7]
    tracker["times"].append(now)
    
    if len(tracker["times"]) >= 6:
        tracker["times"] = [] 
        chat_id = message_or_call.message.chat.id if hasattr(message_or_call, 'message') else message_or_call.chat.id
        
        if tracker["warnings"] == 0:
            tracker["warnings"] = 1
            bot.send_message(chat_id, "⚠️ Warning: Stop spamming! Slow down.")
        elif tracker["warnings"] == 1:
            tracker["warnings"] = 2
            tracker["muted_until"] = now + 300
            bot.send_message(chat_id, "🔇 You are muted for 5 minutes.")
        else:
            users_col.update_one({"_id": str(user_id)}, {"$set": {"banned": True}})
            bot.send_message(chat_id, "⛔ You are permanently banned for spamming.")
            bot.send_message(ADMIN_ID, f"🚨 USER BANNED: {user_id}")
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
    markup.add("Manage Media ⚙️", "Statistics 📊")
    markup.add("Create Coupon 🎫", "Unban User 🔓")
    markup.add("Exit Admin Mode ❌")
    return markup

# --- ADMIN COMMANDS ---
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id == ADMIN_ID:
        bot.send_message(message.chat.id, "Admin Mode: Enabled. You can now send photos or videos directly to add them to the database.", reply_markup=get_admin_markup())
    else:
        bot.send_message(message.chat.id, "❌ Access denied.")

@bot.message_handler(func=lambda m: m.text == "Statistics 📊" and m.from_user.id == ADMIN_ID)
def show_stats(message):
    try:
        u_count = users_col.count_documents({})
        p_count = media_col.count_documents({"type": "photo"})
        v_count = media_col.count_documents({"type": "video"})
        bot.send_message(ADMIN_ID, f"📊 Database Statistics:\n👥 Total Users: {u_count}\n📷 Photos: {p_count}\n🎥 Videos: {v_count}")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Database Error: {e}")

# --- MEDIA UPLOAD (ADMIN ONLY) ---
@bot.message_handler(content_types=['photo', 'video'])
def handle_upload(message):
    if message.from_user.id != ADMIN_ID: 
        bot.send_message(message.chat.id, "❌ You do not have permission to upload media.")
        return
    
    try:
        new_id = get_next_media_id()
        if message.content_type == 'photo':
            media_col.insert_one({"_id": new_id, "file_id": message.photo[-1].file_id, "type": "photo", "category": None})
        elif message.content_type == 'video':
            duration = message.video.duration
            cat = "10" if duration <= 10 else "30" if duration <= 30 else "all"
            media_col.insert_one({"_id": new_id, "file_id": message.video.file_id, "type": "video", "category": cat})
        
        if not message.media_group_id:
            bot.send_message(ADMIN_ID, f"✅ Media successfully added! (ID: {new_id})")
        elif message.media_group_id not in admin_media_groups:
            admin_media_groups.add(message.media_group_id)
            bot.send_message(ADMIN_ID, "✅ Media album added successfully.")
            if len(admin_media_groups) > 100: admin_media_groups.clear()
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Failed to add media. Database error: {e}")

# --- USER ACTIONS ---
@bot.message_handler(func=lambda m: m.text in ["Get Photo 📷 (1 🪙)", "Get Video 🎥", "My Balance 💰", "Buy Coins 🪙", "Enter Promo Code 🎫"])
def user_menu_logic(message):
    if is_spamming(message): return
    uid = message.from_user.id
    
    try:
        user = get_user(uid)
        
        if message.text == "Get Photo 📷 (1 🪙)":
            if uid != ADMIN_ID and user['balance'] < 1:
                return bot.send_message(message.chat.id, "❌ Not enough coins!")
            
            # Empty DB check
            if media_col.count_documents({"type": "photo"}) == 0:
                return bot.send_message(message.chat.id, "❌ Sorry, there are no photos in the database yet.")

            res = list(media_col.aggregate([{"$match": {"type": "photo"}}, {"$sample": {"size": 1}}]))
            media = res[0]
            
            if uid != ADMIN_ID:
                users_col.update_one({"_id": str(uid)}, {"$inc": {"balance": -1}})
            
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Report 🚩", callback_data=f"report_{media['_id']}"))
            bot.send_photo(message.chat.id, media["file_id"], caption=f"ID: {media['_id']}", reply_markup=markup)

        elif message.text == "Get Video 🎥":
            # Empty DB check
            if media_col.count_documents({"type": "video"}) == 0:
                return bot.send_message(message.chat.id, "❌ Sorry, there are no videos in the database yet.")
            
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("0-10s (2 🪙)", callback_data="buy_vid_10"),
                       types.InlineKeyboardButton("11-30s (3 🪙)", callback_data="buy_vid_30"),
                       types.InlineKeyboardButton("31s+ (5 🪙)", callback_data="buy_vid_all"))
            bot.send_message(message.chat.id, "Select video duration:", reply_markup=markup)

        elif message.text == "My Balance 💰":
            bot.send_message(message.chat.id, f"💰 Your balance: {user['balance']} coins.")

        elif message.text == "Enter Promo Code 🎫":
            user_states[uid] = {"state": "wait_promo_input"}
            bot.send_message(message.chat.id, "Please type your promo code:")

        elif message.text == "Buy Coins 🪙":
            markup = types.InlineKeyboardMarkup()
            prices = {"10": 8, "50": 40, "100": 80, "150": 100, "200": 160}
            for c, s in prices.items():
                markup.add(types.InlineKeyboardButton(text=f"{c} Coins for {s} ⭐️", callback_data=f"pay_{c}"))
            bot.send_message(message.chat.id, "🪙 Coin Shop:", reply_markup=markup)
            
    except Exception as e:
        bot.send_message(message.chat.id, "❌ An error occurred. Please try again later.")
        print(f"Error in user_menu_logic: {e}")

# --- CALLBACKS (INLINE BUTTONS) ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    if is_spamming(call): return
    uid_str = str(call.from_user.id)
    
    try:
        if call.data.startswith("buy_vid_"):
            cat = call.data.split("_")[2]
            prices = {"10": 2, "30": 3, "all": 5}
            price = prices[cat]
            user = get_user(call.from_user.id)

            if call.from_user.id != ADMIN_ID and user["balance"] < price:
                return bot.answer_callback_query(call.id, "❌ Not enough coins!", show_alert=True)
                
            res = list(media_col.aggregate([{"$match": {"type": "video", "category": cat}}, {"$sample": {"size": 1}}]))
            if not res:
                return bot.answer_callback_query(call.id, f"❌ No videos available in this category yet.", show_alert=True)
                
            if call.from_user.id != ADMIN_ID:
                users_col.update_one({"_id": uid_str}, {"$inc": {"balance": -price}})
                
            media = res[0]
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Report 🚩", callback_data=f"report_{media['_id']}"))
            bot.send_video(call.message.chat.id, media["file_id"], caption=f"ID: {media['_id']}", reply_markup=markup)
            bot.answer_callback_query(call.id)

        elif call.data.startswith("delete_") and call.from_user.id == ADMIN_ID:
            mid = call.data.split("_")[1]
            media_col.delete_one({"_id": mid})
            bot.answer_callback_query(call.id, "Deleted successfully.")
            bot.edit_message_caption("❌ Media Deleted", call.message.chat.id, call.message.message_id)

        elif call.data.startswith("report_"):
            user_states[call.from_user.id] = {"state": "waiting_for_report", "media_id": call.data.split("_")[1]}
            bot.send_message(call.message.chat.id, "Please type the reason for your report:")
            bot.answer_callback_query(call.id)

        elif call.data.startswith("pay_"):
            amt = call.data.split("_")[1]
            p = {"10": 8, "50": 40, "100": 80, "150": 100, "200": 160}
            bot.send_invoice(call.message.chat.id, "Coins", f"Purchase {amt} 🪙", f"coins_{amt}", "", "XTR", [types.LabeledPrice("XTR", p[amt])])
            bot.answer_callback_query(call.id)
            
    except Exception as e:
        bot.answer_callback_query(call.id, "❌ Error processing request.", show_alert=True)
        print(f"Error in callbacks: {e}")

# --- ADMIN STATE MANAGEMENT & PROMO CODES ---
@bot.message_handler(func=lambda m: m.from_user.id in user_states)
def state_handler(message):
    uid = message.from_user.id
    state_data = user_states[uid]
    state = state_data["state"]
    
    if uid == ADMIN_ID:
        if state == "wait_coupon_name":
            user_states[uid] = {"state": "wait_coupon_value", "name": message.text.upper()}
            bot.send_message(ADMIN_ID, "Now enter the coin amount for this promo code:")
        elif state == "wait_coupon_value":
            if message.text.isdigit():
                coupons_col.update_one({"_id": user_states[uid]["name"]}, {"$set": {"amount": int(message.text)}}, upsert=True)
                bot.send_message(ADMIN_ID, "✅ Promo code created successfully!")
                del user_states[uid]
            else: 
                bot.send_message(ADMIN_ID, "❌ Please enter a valid number.")
        elif state == "wait_unban_id":
            target_id = message.text.strip()
            if target_id.isdigit():
                res = users_col.update_one({"_id": target_id}, {"$set": {"banned": False}})
                if res.modified_count > 0:
                    bot.send_message(ADMIN_ID, f"✅ User {target_id} has been unbanned.")
                else:
                    bot.send_message(ADMIN_ID, "❌ User not found or not banned.")
                del user_states[uid]
            else:
                bot.send_message(ADMIN_ID, "❌ Please enter a valid numerical ID.")
        elif state == "waiting_for_admin_id":
            mid = message.text.strip()
            media = media_col.find_one({"_id": mid})
            if media:
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("Delete Media 🗑", callback_data=f"delete_{mid}"))
                if media["type"] == "photo": bot.send_photo(ADMIN_ID, media["file_id"], caption=f"ID: {mid}", reply_markup=markup)
                else: bot.send_video(ADMIN_ID, media["file_id"], caption=f"ID: {mid}", reply_markup=markup)
            else: 
                bot.send_message(ADMIN_ID, "❌ Media ID not found.")
            del user_states[uid]
    else:
        if is_spamming(message): return
        if state == "wait_promo_input":
            code = message.text.strip().upper()
            user = get_user(uid)
            coupon = coupons_col.find_one({"_id": code})
            
            if coupon:
                if code in user.get("used_coupons", []):
                    bot.send_message(message.chat.id, "❌ You have already used this promo code.")
                else:
                    amount = coupon["amount"]
                    users_col.update_one({"_id": str(uid)}, {"$inc": {"balance": amount}, "$push": {"used_coupons": code}})
                    bot.send_message(message.chat.id, f"✅ Success! You received {amount} 🪙.")
            else:
                bot.send_message(message.chat.id, "❌ Invalid promo code.")
            del user_states[uid]
            
        elif state == "waiting_for_report":
            username = f"@{message.from_user.username}" if message.from_user.username else "Unknown"
            bot.send_message(ADMIN_ID, f"🚩 REPORT\nUser: {username} ({uid})\nMedia ID: {state_data['media_id']}\nReason: {message.text}")
            bot.send_message(message.chat.id, "✅ Report sent to admin. Thank you!")
            del user_states[uid]

@bot.message_handler(func=lambda m: m.text == "Create Coupon 🎫" and m.from_user.id == ADMIN_ID)
def admin_coupon_start(message):
    user_states[ADMIN_ID] = {"state": "wait_coupon_name"}
    bot.send_message(ADMIN_ID, "Enter the name for the new promo code:")

@bot.message_handler(func=lambda m: m.text == "Unban User 🔓" and m.from_user.id == ADMIN_ID)
def admin_unban_start(message):
    user_states[ADMIN_ID] = {"state": "wait_unban_id"}
    bot.send_message(ADMIN_ID, "Enter the User ID to unban:")

@bot.message_handler(func=lambda m: m.text == "Manage Media ⚙️" and m.from_user.id == ADMIN_ID)
def manage_media_start(message):
    user_states[ADMIN_ID] = {"state": "waiting_for_admin_id"}
    bot.send_message(ADMIN_ID, "Enter Media ID to view or delete:")

@bot.message_handler(func=lambda m: m.text == "Exit Admin Mode ❌" and m.from_user.id == ADMIN_ID)
def exit_admin(message):
    bot.send_message(message.chat.id, "Exited Admin Mode.", reply_markup=get_user_markup())

# --- PAYMENTS ---
@bot.pre_checkout_query_handler(func=lambda q: True)
def checkout(q): 
    bot.answer_pre_checkout_query(q.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def pay_ok(message):
    amt = int(message.successful_payment.invoice_payload.split("_")[1])
    users_col.update_one({"_id": str(message.from_user.id)}, {"$inc": {"balance": amt}}, upsert=True)
    bot.send_message(message.chat.id, f"✅ Payment successful! Added {amt} 🪙 to your balance.")

# --- START COMMAND ---
@bot.message_handler(commands=['start'])
def welcome(message):
    get_user(message.from_user.id)
    if message.from_user.id == ADMIN_ID:
        bot.send_message(message.chat.id, f"Hello Admin!\nYour ID is confirmed as: {message.from_user.id}\nSelect an option below:", reply_markup=get_user_markup())
    else:
        bot.send_message(message.chat.id, "Hello! Select an option below to get started:", reply_markup=get_user_markup())

# --- RUN ---
if __name__ == '__main__':
    threading.Thread(target=run_web_server, daemon=True).start()
    bot.infinity_polling(timeout=20, long_polling_timeout=15)
