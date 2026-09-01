# -*- coding: utf-8 -*-
import logging
import json
import os
import sqlite3
import random
import uuid
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, Poll
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
    JobQueue
)
from google import genai

# Load environment variables
load_dotenv()

# Logging Setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Initialize Gemini Client if Key exists
ai_client = None
if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)

# --- DATABASE SETUP ---
DB_NAME = "quiz_bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Table to store Quiz Configurations
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quizzes (
            quiz_id TEXT PRIMARY KEY,
            creator_id INTEGER,
            title TEXT,
            topic TEXT,
            q_count INTEGER,
            language TEXT,
            difficulty TEXT,
            options_count INTEGER,
            time_limit INTEGER,
            shuffle TEXT,
            negative REAL,
            questions_json TEXT
        )
    """)
    # Table to store Live Game Sessions per User
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS active_sessions (
            user_id INTEGER PRIMARY KEY,
            quiz_id TEXT,
            current_q_index INTEGER,
            score REAL,
            answers_json TEXT
        )
    """)
    # Table to store Group Game Sessions
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS group_sessions (
            session_id TEXT PRIMARY KEY,
            quiz_id TEXT,
            group_id INTEGER,
            current_q_index INTEGER,
            poll_message_id INTEGER,
            started INTEGER,
            started_at DATETIME,
            participants_json TEXT
        )
    """)
    # Table to store Group Participants
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS group_participants (
            session_id TEXT,
            user_id INTEGER,
            username TEXT,
            score REAL,
            answered INTEGER,
            PRIMARY KEY(session_id, user_id)
        )
    """)
    # Table to store Leaderboard Data
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leaderboard (
            quiz_id TEXT,
            user_id INTEGER,
            username TEXT,
            score REAL,
            total_questions INTEGER,
            timestamp DATETIME,
            PRIMARY KEY(quiz_id, user_id)
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- CONVERSATION STATES ---
(TOPIC, Q_COUNT, TITLE, DESCRIPTION, LANGUAGE, 
 EXPLANATION, DIFFICULTY, OPTIONS_COUNT, TIME_LIMIT, SHUFFLE, NEGATIVE) = range(11)

# AI Question Generator helper
def generate_bulk_questions_ai(topic, count, lang, difficulty, options_cnt):
    if not ai_client:
        return mock_questions(topic, count, options_cnt)
    
    prompt = f"""
    Generate exactly {count} multiple-choice quiz questions about '{topic}' in the '{lang}' language (or script format).
    Difficulty level should be: {difficulty}.
    Each question must have exactly {options_cnt} options.
    Identify the correct option using its 0-based index.
    
    You MUST respond ONLY with a valid JSON Array matching this structure:
    [
        {{
            "question": "Sawal Text Here",
            "options": ["Option 1", "Option 2", ...],
            "correct": 0
        }}
    ]
    Do not wrap the response in markdown formatting or ```json blocks. Return raw JSON text only.
    """
    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        clean_text = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(clean_text)
    except Exception as e:
        logger.error(f"AI Generation Failed: {e}")
        return mock_questions(topic, count, options_cnt)

def mock_questions(topic, count, options_cnt):
    questions = []
    for i in range(1, count + 1):
        opts = [f"Option {j}" for j in range(1, options_cnt + 1)]
        correct_idx = random.randint(0, options_cnt - 1)
        opts[correct_idx] = f"Correct Option {correct_idx + 1}"
        questions.append({
            "question": f"Sample dynamic question {i} about {topic}?",
            "options": opts,
            "correct": correct_idx
        })
    return questions

# --- BOT ROUTINES & HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if args and args[0].startswith("quiz_"):
        quiz_id = args[0].replace("quiz_", "")
        await start_quiz_game(update, context, quiz_id)
        return

    await update.message.reply_text(
        "🤖 **Welcome to AI Auto-Quiz Generator Bot!**\n\n"
        "⚡ Commands Layout:\n"
        "👉 `/autoquiz` - Naya AI Quiz generate karne ki step-by-step process shuru karein.\n"
        "👉 `/leaderboard` - Sabhi quizzes ke global rank records dekhne ke liye.",
        parse_mode="Markdown"
    )

async def autoquiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "🤖 **AI Auto-Quiz Configuration Wizard**\n\n"
        "📝 **Step 1:** Send me the Topic or Subject for the quiz.\n"
        "(Example: Ancient History, Python Coding, Geography...)",
        parse_mode="Markdown"
    )
    return TOPIC

async def handle_topic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['topic'] = update.message.text
    reply_keyboard = [['5', '10', '20']]
    await update.message.reply_text(
        f"✅ Topic Saved: *{context.user_data['topic']}*\n\n"
        "🔢 **Step 2:** How many questions do you want?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return Q_COUNT

async def handle_q_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['q_count'] = int(update.message.text)
    await update.message.reply_text(
        f"✅ Questions Count: *{context.user_data['q_count']}*\n\n"
        "📝 **Step 3:** Send me the Title of your quiz.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    return TITLE

async def handle_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['title'] = update.message.text
    await update.message.reply_text(
        "✅ Title Saved!\n\n"
        "📝 **Step 4:** Send a Description for this quiz.\n"
        "(Or type `/skipauto` to leave it blank)"
    )
    return DESCRIPTION

async def handle_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    context.user_data['description'] = "None" if text == "/skipauto" else text
    reply_keyboard = [['English', 'Hindi', 'Hinglish']]
    await update.message.reply_text(
        "🌐 **Step 5 — Language**\nChoose quiz output layout language:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return LANGUAGE

async def handle_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['language'] = update.message.text
    reply_keyboard = [['With Explanation', 'No Explanation']]
    await update.message.reply_text(
        "🧾 **Step 6 — Explanation**\nDo you want explanations?",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return EXPLANATION

async def handle_explanation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['explanation'] = update.message.text
    reply_keyboard = [['Easy', 'Medium', 'Hard']]
    await update.message.reply_text(
        "🎚 **Step 7 — Difficulty**\nChoose calculation difficulty:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return DIFFICULTY

async def handle_difficulty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['difficulty'] = update.message.text
    reply_keyboard = [['2 Options', '4 Options']]
    await update.message.reply_text(
        "🛛 **Step 8 — Option Count**\nHow many choices per card?",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return OPTIONS_COUNT

async def handle_options_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['options_count'] = int(update.message.text.split()[0])
    reply_keyboard = [['15 sec', '30 sec', '60 sec']]
    await update.message.reply_text(
        "⏱ **Step 9 — Time Limit**\nSet ticker duration:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return TIME_LIMIT

async def handle_time_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['time_limit'] = int(update.message.text.split()[0])
    reply_keyboard = [['Shuffle All', 'No Shuffle']]
    await update.message.reply_text(
        "🔀 **Step 10 — Shuffle**\nMix questions sequence order?",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return SHUFFLE

async def handle_shuffle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['shuffle'] = update.message.text
    reply_keyboard = [['0', '0.25', '0.33', '0.50']]
    await update.message.reply_text(
        "➖ **Step 11 — Negative Marking**\nDeduct offset per error choice:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return NEGATIVE

# Final Summary aur Quiz Generation Confirmation
async def handle_negative_and_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['negative'] = float(update.message.text)
    
    # Saare data ko variables me extract karna
    data = context.user_data
    creator_id = update.effective_user.id
    
    # Loading message
    await update.message.reply_text("⏳ **Generating AI Questions... Please wait!**", parse_mode="Markdown")
    
    # AI se questions generate karna
    questions = generate_bulk_questions_ai(
        topic=data.get('topic'),
        count=data.get('q_count'),
        lang=data.get('language'),
        difficulty=data.get('difficulty'),
        options_cnt=data.get('options_count')
    )
    
    # Unique Quiz ID generate karna
    quiz_id = f"quiz_{uuid.uuid4().hex[:12]}"
    
    # Database mein quiz save karna
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO quizzes 
        (quiz_id, creator_id, title, topic, q_count, language, difficulty, options_count, time_limit, shuffle, negative, questions_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        quiz_id,
        creator_id,
        data.get('title'),
        data.get('topic'),
        data.get('q_count'),
        data.get('language'),
        data.get('difficulty'),
        data.get('options_count'),
        data.get('time_limit'),
        data.get('shuffle'),
        data.get('negative'),
        json.dumps(questions)
    ))
    conn.commit()
    conn.close()
    
    # Markdown ko hata kar HTML formatting use kar rahe hain taaki crash na ho
    summary = (
        "<b>🎉 AI Quiz Generated Successfully!</b>\n\n"
        f"🏷 <b>Title:</b> {data.get('title')}\n"
        f"📝 <b>Questions:</b> {data.get('q_count')}\n"
        f"🌐 <b>Language:</b> {data.get('language')}\n"
        f"🎚 <b>Difficulty:</b> {data.get('difficulty')}\n"
        f"🎛 <b>Options/Q:</b> {data.get('options_count')}\n"
        f"🧾 <b>Explanation:</b> {data.get('explanation')}\n"
        f"⏱ <b>Time/Q:</b> {data.get('time_limit')} sec\n"
        f"🔀 <b>Shuffle:</b> {data.get('shuffle')}\n"
        f"➖ <b>Negative:</b> {data.get('negative')}\n\n"
        "<b>🎮 Ready to Play!</b>"
    )
    
    # Inline buttons for Private Chat and Group Chat
    keyboard = [
        [InlineKeyboardButton("🎮 Start Quiz in Private", callback_data=f"start_private_{quiz_id}")],
        [InlineKeyboardButton("👥 Start Quiz in Group", callback_data=f"start_group_{quiz_id}")],
        [InlineKeyboardButton("🔗 Share Group Link", callback_data=f"share_link_{quiz_id}")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    
    # parse_mode ko "HTML" kar diya gaya hai
    await update.message.reply_text(summary, parse_mode="HTML", reply_markup=markup)
    
    return ConversationHandler.END

async def handle_start_quiz_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for 'Start Quiz in Private' button"""
    query = update.callback_query
    await query.answer()
    
    # quiz_id ko extract karna callback_data se
    quiz_id = query.data.replace("start_private_", "")
    
    # Quiz game start karna private chat mein
    await start_quiz_game(query, context, quiz_id)

async def handle_start_quiz_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for 'Start Quiz in Group' button"""
    query = update.callback_query
    await query.answer()
    
    # quiz_id ko extract karna callback_data se
    quiz_id = query.data.replace("start_group_", "")
    
    # Bot username get karna
    bot_info = await context.bot.get_me()
    bot_username = bot_info.username
    
    # Group link banao
    group_link = f"https://t.me/{bot_username}?start={quiz_id}"
    
    # Message with group link
    group_message = (
        f"👥 <b>Start Quiz in Group Chat</b>\n\n"
        f"📌 <b>Share this link in your group:</b>\n\n"
        f"<code>{group_link}</code>\n\n"
        f"✅ Group members ko link par click karna hoga aur quiz start hoga!"
    )
    
    await query.edit_message_text(group_message, parse_mode="HTML")

async def handle_share_group_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback handler for 'Share Group Link' button"""
    query = update.callback_query
    await query.answer()
    
    # quiz_id ko extract karna callback_data se
    quiz_id = query.data.replace("share_link_", "")
    
    # Bot username get karna
    bot_info = await context.bot.get_me()
    bot_username = bot_info.username
    
    # Group link banao
    group_link = f"https://t.me/{bot_username}?start={quiz_id}"
    
    # Message with shareable link
    share_message = (
        f"🔗 <b>Quiz Share Link</b>\n\n"
        f"<b>Copy this link aur apne group mein share karo:</b>\n\n"
        f"<code>{group_link}</code>\n\n"
        f"📱 <b>Or use the button below to share directly:</b>"
    )
    
    # Share button
    keyboard = [
        [InlineKeyboardButton("📤 Share to Group", url=f"https://t.me/share/url?url={group_link}&text=🎯%20Quiz%20Challenge!")],
        [InlineKeyboardButton("↩️ Back", callback_data=f"back_{quiz_id}")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(share_message, parse_mode="HTML", reply_markup=markup)

async def handle_back_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to quiz summary"""
    query = update.callback_query
    await query.answer()
    
    quiz_id = query.data.replace("back_", "")
    
    # Quiz details get karna database se
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT title, q_count, language, difficulty, options_count, negative, time_limit, shuffle FROM quizzes WHERE quiz_id = ?", (quiz_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        title, q_count, language, difficulty, options_count, negative, time_limit, shuffle = row
        
        summary = (
            "<b>🎉 AI Quiz Generated Successfully!</b>\n\n"
            f"🏷 <b>Title:</b> {title}\n"
            f"📝 <b>Questions:</b> {q_count}\n"
            f"🌐 <b>Language:</b> {language}\n"
            f"🎚 <b>Difficulty:</b> {difficulty}\n"
            f"🎛 <b>Options/Q:</b> {options_count}\n"
            f"⏱ <b>Time/Q:</b> {time_limit} sec\n"
            f"🔀 <b>Shuffle:</b> {shuffle}\n"
            f"➖ <b>Negative:</b> {negative}\n\n"
            "<b>🎮 Ready to Play!</b>"
        )
        
        keyboard = [
            [InlineKeyboardButton("🎮 Start Quiz in Private", callback_data=f"start_private_{quiz_id}")],
            [InlineKeyboardButton("👥 Start Quiz in Group", callback_data=f"start_group_{quiz_id}")],
            [InlineKeyboardButton("🔗 Share Group Link", callback_data=f"share_link_{quiz_id}")]
        ]
        markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(summary, parse_mode="HTML", reply_markup=markup)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Quiz setup processing setup abandoned.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# --- GROUP GAME IMPLEMENTATION ---

async def start_group_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE, quiz_id: str):
    """Start group quiz session"""
    group_id = update.effective_chat.id
    
    # Quiz details get karna
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT title, questions_json, time_limit FROM quizzes WHERE quiz_id = ?", (quiz_id,))
    row = cursor.fetchone()
    
    if not row:
        await update.message.reply_text("❌ Quiz nahi mila!")
        conn.close()
        return
    
    title, questions_json, time_limit = row
    
    # Session ID generate karna
    session_id = f"session_{uuid.uuid4().hex[:12]}"
    
    # Group session create karna
    cursor.execute("""
        INSERT INTO group_sessions (session_id, quiz_id, group_id, current_q_index, started, participants_json)
        VALUES (?, ?, ?, 0, 0, ?)
    """, (session_id, quiz_id, group_id, json.dumps({})))
    conn.commit()
    conn.close()
    
    # "I am Ready" panel message
    ready_message = (
        f"<b>🎮 {title}</b>\n\n"
        f"📋 Participants ko neeche 'I am Ready' button par tap karna hai\n"
        f"✅ Jab minimum 1 user ready ho jayega, quiz start hoga!\n\n"
        f"<b>Ready Participants: 0</b>"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ I am Ready!", callback_data=f"ready_{session_id}")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    
    # Store session info in context
    context.chat_data['active_session'] = session_id
    context.chat_data['quiz_id'] = quiz_id
    
    msg = await update.message.reply_text(ready_message, parse_mode="HTML", reply_markup=markup)
    
    # Store message ID
    context.chat_data['ready_message_id'] = msg.message_id

async def handle_ready_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'I am Ready' button click"""
    query = update.callback_query
    await query.answer()
    
    session_id = query.data.replace("ready_", "")
    user_id = query.from_user.id
    username = query.from_user.first_name or "User"
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Check if user already added
    cursor.execute("SELECT * FROM group_participants WHERE session_id = ? AND user_id = ?", (session_id, user_id))
    if cursor.fetchone():
        conn.close()
        await query.answer("❌ Tum pehle se ready ho!", show_alert=True)
        return
    
    # Add participant
    cursor.execute("""
        INSERT INTO group_participants (session_id, user_id, username, score, answered)
        VALUES (?, ?, ?, 0.0, 0)
    """, (session_id, user_id, username))
    
    # Get all participants count
    cursor.execute("SELECT COUNT(*) FROM group_participants WHERE session_id = ?", (session_id,))
    count = cursor.fetchone()[0]
    conn.commit()
    
    # Get group_id and quiz_id
    cursor.execute("SELECT group_id, quiz_id, started FROM group_sessions WHERE session_id = ?", (session_id,))
    group_data = cursor.fetchone()
    conn.close()
    
    if not group_data:
        return
    
    group_id, quiz_id, started = group_data
    
    # Update ready message
    ready_message = (
        f"<b>🎮 Group Quiz</b>\n\n"
        f"📋 Participants ko 'I am Ready' button par tap karna hai\n"
        f"✅ Jab minimum 1 user ready ho jayega, quiz shuru ho jayega!\n\n"
        f"<b>Ready Participants: {count}</b>"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ I am Ready!", callback_data=f"ready_{session_id}")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    
    # Edit ready message
    if context.chat_data.get('ready_message_id'):
        try:
            await context.bot.edit_message_text(
                chat_id=group_id,
                message_id=context.chat_data['ready_message_id'],
                text=ready_message,
                parse_mode="HTML",
                reply_markup=markup
            )
        except:
            pass
    
    # Auto start when first user is ready
    if count == 1 and started == 0:
        await start_group_game(context, session_id, quiz_id, group_id)

async def start_group_game(context: ContextTypes.DEFAULT_TYPE, session_id: str, quiz_id: str, group_id: int):
    """Start the actual group game"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Mark as started
    cursor.execute("UPDATE group_sessions SET started = 1, started_at = ? WHERE session_id = ?", 
                  (datetime.now(), session_id))
    
    # Get quiz details
    cursor.execute("SELECT questions_json, time_limit FROM quizzes WHERE quiz_id = ?", (quiz_id,))
    quiz_data = cursor.fetchone()
    questions = json.loads(quiz_data[0])
    time_limit = quiz_data[1]
    conn.commit()
    conn.close()
    
    # Send first question as poll
    await send_group_poll(context, session_id, quiz_id, group_id, questions, 0, time_limit)

async def send_group_poll(context: ContextTypes.DEFAULT_TYPE, session_id: str, quiz_id: str, group_id: int, questions: list, q_idx: int, time_limit: int):
    """Send poll for group quiz"""
    if q_idx >= len(questions):
        # Quiz complete
        await end_group_game(context, session_id, quiz_id, group_id)
        return
    
    q = questions[q_idx]
    question_text = f"❓ Q{q_idx + 1}/{len(questions)}: {q['question']}"
    
    # Send poll
    poll_msg = await context.bot.send_poll(
        chat_id=group_id,
        question=question_text,
        options=q['options'],
        type=Poll.QUIZ,
        correct_option_id=q['correct'],
        is_anonymous=False,
        open_period=time_limit,
        explanation="✅ Sahi jawab!"
    )
    
    # Store poll info
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE group_sessions 
        SET current_q_index = ?, poll_message_id = ? 
        WHERE session_id = ?
    """, (q_idx, poll_msg.message_id, session_id))
    conn.commit()
    conn.close()
    
    # Schedule next question after time_limit
    context.job_queue.run_once(
        process_poll_and_next_question,
        when=timedelta(seconds=time_limit),
        data={'session_id': session_id, 'quiz_id': quiz_id, 'group_id': group_id, 'q_idx': q_idx, 'questions': questions}
    )

async def process_poll_and_next_question(context: ContextTypes.DEFAULT_TYPE):
    """Process current poll and send next question"""
    data = context.job.data
    session_id = data['session_id']
    quiz_id = data['quiz_id']
    group_id = data['group_id']
    q_idx = data['q_idx']
    questions = data['questions']
    
    # Get time limit for next question
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT time_limit FROM quizzes WHERE quiz_id = ?", (quiz_id,))
    time_limit = cursor.fetchone()[0]
    conn.close()
    
    # Send next question
    await send_group_poll(context, session_id, quiz_id, group_id, questions, q_idx + 1, time_limit)

async def end_group_game(context: ContextTypes.DEFAULT_TYPE, session_id: str, quiz_id: str, group_id: int):
    """End group game and show leaderboard"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Get all participants
    cursor.execute("""
        SELECT user_id, username, score 
        FROM group_participants 
        WHERE session_id = ? 
        ORDER BY score DESC 
        LIMIT 10
    """, (session_id,))
    
    results = cursor.fetchall()
    
    # Clean up session
    cursor.execute("DELETE FROM group_sessions WHERE session_id = ?", (session_id,))
    cursor.execute("DELETE FROM group_participants WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()
    
    # Show leaderboard
    leaderboard_text = "<b>🏆 Quiz Complete! Top 10 Results</b>\n\n"
    
    if results:
        for idx, (user_id, username, score) in enumerate(results, 1):
            leaderboard_text += f"{idx}. <b>{username}</b> → Score: <b>{score}</b>\n"
    else:
        leaderboard_text += "❌ No participants!"
    
    await context.bot.send_message(
        chat_id=group_id,
        text=leaderboard_text,
        parse_mode="HTML"
    )

# --- ACTIVE GAMEPLAY LOOP IMPLEMENTATION ---

async def start_quiz_game(update: Update, context: ContextTypes.DEFAULT_TYPE, quiz_id: str):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Check if group or private
    if update.effective_chat.type in ['group', 'supergroup']:
        # Group quiz
        await start_group_quiz(update, context, quiz_id)
        return
    
    # Private quiz
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT title, questions_json FROM quizzes WHERE quiz_id = ?", (quiz_id,))
    row = cursor.fetchone()
    
    if not row:
        await update.message.reply_text("❌ Galat link! Yeh quiz database me nahi mila.")
        conn.close()
        return

    title, questions_json = row
    questions = json.loads(questions_json)

    cursor.execute("""
        INSERT OR REPLACE INTO active_sessions (user_id, quiz_id, current_q_index, score, answers_json)
        VALUES (?, ?, 0, 0.0, ?)
    """, (user_id, quiz_id, json.dumps([])))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"🏁 **Welcome! Starting Game Room Session**\nTitle: *{title}*\n\nTariyar ho jaiye! Pehla question aa raha hai...", parse_mode="Markdown")
    await send_next_game_question(user_id, context, update)

async def send_next_game_question(user_id: int, context: ContextTypes.DEFAULT_TYPE, update: Update = None, callback_query = None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT quiz_id, current_q_index, score FROM active_sessions WHERE user_id = ?", (user_id,))
    session = cursor.fetchone()
    
    if not session:
        conn.close()
        return

    quiz_id, q_idx, score = session
    
    cursor.execute("SELECT title, questions_json, negative FROM quizzes WHERE quiz_id = ?", (quiz_id,))
    quiz_data = cursor.fetchone()
    title, questions_json, negative = quiz_data
    questions = json.loads(questions_json)

    if q_idx >= len(questions):
        username = "User"
        if update and update.effective_user.username:
            username = f"@{update.effective_user.username}"
        elif callback_query and callback_query.from_user.username:
            username = f"@{callback_query.from_user.username}"

        cursor.execute("""
            INSERT OR REPLACE INTO leaderboard (quiz_id, user_id, username, score, total_questions, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (quiz_id, user_id, username, score, len(questions), datetime.now()))
        
        cursor.execute("DELETE FROM active_sessions WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

        score_text = f"🏁 **Quiz Complete!**\n\nAapka Final Score: *{score}/{len(questions)}*\n🏆 Live standings dekhne ke liye `/leaderboard` run karein."
        if callback_query:
            await callback_query.message.reply_text(score_text, parse_mode="Markdown")
        else:
            await update.message.reply_text(score_text, parse_mode="Markdown")
        return

    q = questions[q_idx]
    keyboard = []
    for idx, opt in enumerate(q['options']):
        keyboard.append([InlineKeyboardButton(opt, callback_data=f"ans_{idx}")])
        
    markup = InlineKeyboardMarkup(keyboard)
    msg_text = f"❓ **Question {q_idx + 1}/{len(questions)}**:\n\n{q['question']}"

    if callback_query:
        await callback_query.message.reply_text(msg_text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg_text, reply_markup=markup, parse_mode="Markdown")
        
    conn.close()

async def handle_game_answer_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT quiz_id, current_q_index, score FROM active_sessions WHERE user_id = ?", (user_id,))
    session = cursor.fetchone()
    
    if not session:
        await query.edit_message_text("❌ active session nahi mila. Kripya naya session shuru karein.")
        conn.close()
        return
        
    quiz_id, q_idx, score = session
    
    cursor.execute("SELECT questions_json, negative FROM quizzes WHERE quiz_id = ?", (quiz_id,))
    quiz_data = cursor.fetchone()
    questions = json.loads(quiz_data[0])
    negative = quiz_data[1]
    
    selected_idx = int(query.data.split("_")[1])
    correct_idx = questions[q_idx]['correct']
    
    if selected_idx == correct_idx:
        score += 1.0
        feedback = "✅ **Sahi Jawab! (+1)**"
    else:
        score -= negative
        correct_text = questions[q_idx]['options'][correct_idx]
        feedback = f"❌ **Galat Jawab! (-{negative})**\nSahi jawab: *{correct_text}*"

    await query.edit_message_text(text=f"{query.message.text}\n\n{feedback}", parse_mode="Markdown")
    
    cursor.execute("UPDATE active_sessions SET current_q_index = ?, score = ? WHERE user_id = ?", (q_idx + 1, score, user_id))
    conn.commit()
    conn.close()
    
    await send_next_game_question(user_id, context, callback_query=query)

async def view_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT quiz_id FROM active_sessions WHERE user_id = ?", (update.effective_user.id,))
    session = cursor.fetchone()
    
    if session:
        quiz_id = session[0]
        cursor.execute("""
            SELECT username, score, total_questions FROM leaderboard 
            WHERE quiz_id = ? ORDER BY score DESC LIMIT 10
        """, (quiz_id,))
        title_tag = "Current Active Quiz"
    else:
        cursor.execute("""
            SELECT username, score, total_questions FROM leaderboard 
            ORDER BY score DESC LIMIT 10
        """)
        title_tag = "Global Top Scorers"

    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        await update.message.reply_text("🏆 **Leaderboard abhi khali hai!**\nQuiz complete hone par yahan data show hoga.", parse_mode="Markdown")
        return
        
    text = f"🏆 **Leaderboard - {title_tag}**\n\n"
    for idx, row in enumerate(rows, 1):
        text += f"{idx}. *{row[0]}* ➔ Score: *{row[1]}/{row[2]}*\n"
        
    await update.message.reply_text(text, parse_mode="Markdown")

def main():
    if not TELEGRAM_TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN environment variable config values missing inside project space.")
        return
        
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("autoquiz", autoquiz_start)],
        states={
            TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_topic)],
            Q_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_q_count)],
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_title)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_description)],
            LANGUAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_language)],
            EXPLANATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_explanation)],
            DIFFICULTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_difficulty)],
            OPTIONS_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_options_count)],
            TIME_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_time_limit)],
            SHUFFLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_shuffle)],
            NEGATIVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_negative_and_finish)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("leaderboard", view_leaderboard))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(handle_game_answer_click, pattern="^ans_"))
    application.add_handler(CallbackQueryHandler(handle_start_quiz_private, pattern="^start_private_"))
    application.add_handler(CallbackQueryHandler(handle_start_quiz_group, pattern="^start_group_"))
    application.add_handler(CallbackQueryHandler(handle_share_group_link, pattern="^share_link_"))
    application.add_handler(CallbackQueryHandler(handle_back_button, pattern="^back_"))
    application.add_handler(CallbackQueryHandler(handle_ready_button, pattern="^ready_"))

    print("🚀 Production-ready Interactive Game Engine Started successfully.")
    application.run_polling()

if __name__ == '__main__':
    main()
