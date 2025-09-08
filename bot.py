
from __future__ import annotations
import os
import json
import random
import sqlite3
import textwrap
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import List, Dict, Any, Optional, Tuple

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# =============================
# Config
# =============================
DB_PATH = os.environ.get("DB_PATH", "dailytechq.sqlite3")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TIMEZONE = os.environ.get("TZ", "Asia/Jerusalem")

# Default schedule: 09:00 local time
SCHEDULE_HOUR = int(os.environ.get("SCHEDULE_HOUR", "9"))
SCHEDULE_MIN = int(os.environ.get("SCHEDULE_MIN", "0"))

# Webhook (optional). If WEBHOOK_URL set, will use webhook; else long polling.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEBAPP_HOST = os.environ.get("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT", "8443"))

# =============================
# Sample Questions
# =============================
# You can extend/replace this list from a JSON or API. Each item must have:
# id, category in {"SQL","Algorithms","HTML"}, question, solution (markdown), difficulty
QUESTIONS: List[Dict[str, Any]] = [
    {
        "id": "sql-join-null-1",
        "category": "SQL",
        "difficulty": "easy",
        "question": textwrap.dedent(
            """
            **SQL — INNER vs LEFT JOIN**
            Given tables `orders(order_id, customer_id)` and `customers(customer_id, name)`,
            write a query that returns *all customers* and the number of orders they placed
            (0 if none). Sort by `name`.
            """
        ).strip(),
        "solution": textwrap.dedent(
            """
            ```sql
            SELECT c.customer_id,
                   c.name,
                   COUNT(o.order_id) AS order_count
            FROM customers AS c
            LEFT JOIN orders AS o
              ON o.customer_id = c.customer_id
            GROUP BY c.customer_id, c.name
            ORDER BY c.name;
            ```

            **Why this works**
            - `LEFT JOIN` preserves every row from `customers`, attaching matching `orders` rows when they exist.
            - `COUNT(o.order_id)` counts only non-NULL `order_id`, so customers with no orders get 0.
            - Grouping by the customer keys yields a single row per customer.
            """
        ).strip(),
    },
    {
        "id": "algo-two-sum-2",
        "category": "Algorithms",
        "difficulty": "easy",
        "question": textwrap.dedent(
            """
            **Algorithms — Two Sum (Hash Map)**
            Given an integer array `nums` and an integer `target`, return indices of the two
            numbers that add up to `target`. Assume exactly one solution and no reuse.
            What is the \*O(n)\* approach?
            """
        ).strip(),
        "solution": textwrap.dedent(
            """
            Use a running hash map from value → index.
            ```python
            def two_sum(nums, target):
                seen = {}
                for i, x in enumerate(nums):
                    if target - x in seen:
                        return [seen[target - x], i]
                    seen[x] = i
            ```
            **Why O(n)**: each element is inserted/checked once; dict ops are amortized O(1).
            """
        ).strip(),
    },
    {
        "id": "html-semantics-1",
        "category": "HTML",
        "difficulty": "easy",
        "question": textwrap.dedent(
            """
            **HTML — Semantic Tags**
            Replace generic `<div>`s with semantic HTML for: page header with a logo, a nav bar,
            the main content with an article, and a footer. Give a minimal snippet.
            """
        ).strip(),
        "solution": textwrap.dedent(
            """
            ```html
            <header>
              <img src="/logo.svg" alt="Site logo" />
            </header>
            <nav>
              <a href="/">Home</a>
              <a href="/about">About</a>
            </nav>
            <main>
              <article>
                <h1>Title</h1>
                <p>Body…</p>
              </article>
            </main>
            <footer>© 2025</footer>
            ```
            **Why semantic?** Better accessibility, SEO, and default landmark roles.
            """
        ).strip(),
    },
    {
        "id": "sql-window-avg-1",
        "category": "SQL",
        "difficulty": "medium",
        "question": textwrap.dedent(
            """
            **SQL — Moving Average**
            For table `prices(day DATE, symbol TEXT, close NUMERIC)`, compute a 3-day moving
            average of `close` per symbol, ordered by day, returning `day, symbol, ma3`.
            """
        ).strip(),
        "solution": textwrap.dedent(
            """
            ```sql
            SELECT day,
                   symbol,
                   AVG(close) OVER (
                     PARTITION BY symbol
                     ORDER BY day
                     ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
                   ) AS ma3
            FROM prices;
            ```
            **Why window functions?** They avoid self-joins and let you aggregate over a moving frame.
            """
        ).strip(),
    },
    {
        "id": "algo-bfs-1",
        "category": "Algorithms",
        "difficulty": "medium",
        "question": textwrap.dedent(
            """
            **Algorithms — Shortest Path in Unweighted Graph**
            Describe how to find the shortest path length from a source `s` to all nodes in
            an unweighted graph with adjacency list `G`.
            """
        ).strip(),
        "solution": textwrap.dedent(
            """
            Breadth-First Search (BFS) from `s` keeps a queue and `dist` map.
            ```python
            from collections import deque

            def shortest_paths_unweighted(G, s):
                dist = {s: 0}
                q = deque([s])
                while q:
                    u = q.popleft()
                    for v in G[u]:
                        if v not in dist:
                            dist[v] = dist[u] + 1
                            q.append(v)
                return dist
            ```
            **Why it works**: BFS explores by layers, ensuring the first time you visit a node
            is via the fewest edges.
            """
        ).strip(),
    },
    
    {
        "id": 101,
        "category": "Python",
        "difficulty": "easy",
        "question": "מה ההבדל בין רשימה (list) לבין טופל (tuple) בפייתון?",
        "solution": "list היא mutable (אפשר לשנות ערכים), בעוד tuple היא immutable (לא ניתן לשנות אחרי יצירה)."
    },
    {
        "id": 102,
        "category": "Python",
        "difficulty": "medium",
        "question": "כתוב פונקציה שמקבלת מחרוזת ומחזירה את ספירת המילים בה.",
        "solution": "אפשר להשתמש ב־split() כדי לחלק למילים ואז לקחת len:\n\n```python\ndef word_count(s):\n    return len(s.split())\n```"
    },
    {
        "id": 103,
        "category": "Python",
        "difficulty": "hard",
        "question": "הסבר מה זה list comprehension ותן דוגמה.",
        "solution": "syntactic sugar ליצירת רשימות:\n```python\nsquares = [x*x for x in range(5)]\n```"
    },
    {
        "id": 201,
        "category": "CSS",
        "difficulty": "easy",
        "question": "מה ההבדל בין class selector (`.class`) ל־id selector (`#id`) ב־CSS?",
        "solution": "class יכול להיות בשימוש על מספר אלמנטים, id ייחודי לדף. הסינטקס: `.myclass {}` מול `#myid {}`."
    },
    {
        "id": 202,
        "category": "CSS",
        "difficulty": "medium",
        "question": "איך מגדירים grid layout בסיסי של 3 עמודות ב־CSS?",
        "solution": "```css\n.container {\n  display: grid;\n  grid-template-columns: 1fr 1fr 1fr;\n}\n```"
    },
    {
        "id": 203,
        "category": "CSS",
        "difficulty": "hard",
        "question": "מה זה CSS specificity ואיך נקבע איזה כלל מנצח?",
        "solution": "Specificity נקבע לפי סוג selector: inline > id > class > element. כלל עם ניקוד גבוה יותר מנצח."
    },

]

# =============================
# Persistence Layer (SQLite)
# =============================
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
  user_id INTEGER PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  subscribed INTEGER NOT NULL DEFAULT 1,
  last_question_id TEXT,
  last_sent_date TEXT
);
"""


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db():
    with db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                subscribed INTEGER NOT NULL,
                last_question_id INTEGER,
                last_sent_date TEXT
            )"""
        )
        conn.commit()

        # הוספת העמודה daily_count אם היא לא קיימת
        try:
            conn.execute("ALTER TABLE users ADD COLUMN daily_count INTEGER NOT NULL DEFAULT 1;")
            conn.commit()
        except Exception:
            pass  # אם העמודה כבר קיימת – מתעלמים



# =============================
# Question selection helpers
# =============================
TZ = pytz.timezone(TIMEZONE)


def today_str() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def pick_question(previous_id: Optional[str]) -> Dict[str, Any]:
    pool = [q for q in QUESTIONS if q["id"] != previous_id] or QUESTIONS
    return random.choice(pool)


# =============================
# Bot Handlers
# =============================
WELCOME = (
    "👋 Hi! You'll get technical questions daily (SQL, Algorithms, HTML).\n"
    "Use /subscribe or /unsubscribe. /today to resend today's question.\n"
    "Use /setcount <n> to get n questions per day (1–5). /more <n> to get extra now."
)



async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, chat_id, subscribed) VALUES (?,?,1)",
            (user_id, chat_id),
        )
        conn.commit()
    await update.message.reply_text(WELCOME)


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with db() as conn:
        conn.execute("UPDATE users SET subscribed=1 WHERE user_id=?", (user_id,))
        conn.commit()
    await update.message.reply_text("✅ Subscribed. You'll get daily questions at 09:00.")


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with db() as conn:
        conn.execute("UPDATE users SET subscribed=0 WHERE user_id=?", (user_id,))
        conn.commit()
    await update.message.reply_text("🔕 Unsubscribed. Use /subscribe to rejoin.")


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await send_daily_question_to_user(context.application, user_id)


# Inline button callbacks
SHOW_SOLUTION = "show_solution"
ANOTHER = "another_question"
RESOURCES = "resources"


async def send_question(chat_id: int, q: Dict[str, Any], app: Application) -> None:
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📘 Show solution", callback_data=f"{SHOW_SOLUTION}:{q['id']}")
            ],
            [
                InlineKeyboardButton("🎲 Another", callback_data=f"{ANOTHER}:{q['id']}")
            ],
            [
                InlineKeyboardButton("🔗 Resources", callback_data=f"{RESOURCES}:{q['id']}")
            ],
        ]
    )
    header = f"*Category:* {q['category']}  ·  *Difficulty:* {q['difficulty']}\n\n"
    await app.bot.send_message(chat_id=chat_id, text=header + q["question"], parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def send_daily_question_to_user(app: Application, user_id: int):
    with db() as conn:
        row = conn.execute(
            "SELECT chat_id, subscribed, last_question_id, last_sent_date, "
            "COALESCE(daily_count, 1) as daily_count "
            "FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()
    if not row:
        return
    chat_id, subscribed, last_qid, last_date, daily_count = row
    if not subscribed:
        return

    # אם כבר שלחנו היום – נשלח שוב את הסט (אפשר להשאיר כך או לדלג; כאן שולחים שוב לפי בקשות ידניות)
    previous_id = last_qid if last_date == today_str() else None

    sent_any = False
    for i in range(int(daily_count)):
        q = pick_question(previous_id)
        previous_id = q["id"]
        await send_question(chat_id, q, app)
        sent_any = True

    if sent_any and last_date != today_str():
        with db() as conn:
            conn.execute(
                "UPDATE users SET last_question_id=?, last_sent_date=? WHERE user_id=?",
                (previous_id, today_str(), user_id),
            )
            conn.commit()


async def daily_broadcast(app: Application):
    # Send to all subscribed users
    with db() as conn:
        rows = conn.execute(
            "SELECT user_id FROM users WHERE subscribed=1"
        ).fetchall()
    for (user_id,) in rows:
        try:
            await send_daily_question_to_user(app, user_id)
        except Exception as e:
            print(f"Failed to send to {user_id}: {e}")


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    action, _, qid = data.partition(":")
    q = next((x for x in QUESTIONS if x["id"] == qid), None)
    if not q:
        await query.edit_message_text("Question not found.")
        return

    if action == SHOW_SOLUTION:
        await query.edit_message_text(
            text=q["question"] + "\n\n" + q["solution"], parse_mode=ParseMode.MARKDOWN
        )
    elif action == ANOTHER:
        nxt = pick_question(previous_id=qid)
        await query.edit_message_text(
            text=f"*Category:* {nxt['category']}  ·  *Difficulty:* {nxt['difficulty']}\n\n" + nxt["question"],
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📘 Show solution", callback_data=f"{SHOW_SOLUTION}:{nxt['id']}")],
                [InlineKeyboardButton("🎲 Another", callback_data=f"{ANOTHER}:{nxt['id']}")],
                [InlineKeyboardButton("🔗 Resources", callback_data=f"{RESOURCES}:{nxt['id']}")],
            ]),
        )
    elif action == RESOURCES:
        # Stub: demonstrate API mastery by linking official docs based on category
        links = {
            "SQL": "PostgreSQL docs: https://www.postgresql.org/docs/current/",
            "Algorithms": "CLRS Book Notes (MIT): https://mitpress.mit.edu/9780262046305/",
            "HTML": "MDN Web Docs: https://developer.mozilla.org/en-US/docs/Web/HTML",
        }
        await query.edit_message_text(
            text=q["question"] + "\n\n" + f"Helpful resources → {links.get(q['category'], 'General search')}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📘 Show solution", callback_data=f"{SHOW_SOLUTION}:{q['id']}")],
                [InlineKeyboardButton("🎲 Another", callback_data=f"{ANOTHER}:{q['id']}")],
            ]),
        )


# =============================
# Scheduler bootstrap
# =============================

def schedule_daily(app: Application) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=TIMEZONE)
    trigger = CronTrigger(hour=SCHEDULE_HOUR, minute=SCHEDULE_MIN)

    def job_wrapper():
        # Run within asyncio loop of Application
        app.create_task(daily_broadcast(app))

    sched.add_job(job_wrapper, trigger, name="daily_broadcast")
    sched.start()
    return sched


# =============================
# Main
# =============================
async def post_init(app: Application):
    # Fire once on boot to ensure DB exists
    init_db()


async def cmd_setcount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /setcount <n>  (1–5)")
        return
    try:
        n = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Please provide a number, e.g. /setcount 3")
        return
    if not (1 <= n <= 5):
        await update.message.reply_text("Allowed range is 1–5.")
        return
    with db() as conn:
        conn.execute("UPDATE users SET daily_count=? WHERE user_id=?", (n, user_id))
        conn.commit()
    await update.message.reply_text(f"✅ Daily question count set to {n}.")

async def cmd_more(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # ברירת מחדל: 1; אפשר /more 3 לשלוח שלוש עכשיו
    n = 1
    if context.args:
        try:
            n = max(1, min(5, int(context.args[0])))
        except ValueError:
            pass
    # שולחים n שאלות מיידית
    app = context.application
    previous = None
    for _ in range(n):
        q = pick_question(previous)
        previous = q["id"]
        await send_question(update.effective_chat.id, q, app)




def build_app() -> Application:
    if not TELEGRAM_TOKEN:
        raise SystemExit("Missing TELEGRAM_TOKEN env var")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("setcount", cmd_setcount))
    app.add_handler(CommandHandler("more", cmd_more))


    # Buttons
    app.add_handler(CallbackQueryHandler(on_button))

    return app


if __name__ == "__main__":
    application = build_app()

    # Start daily scheduler
    schedule_daily(application)

    if WEBHOOK_URL:
        # Webhook mode (requires a public HTTPS endpoint)
        print(f"Starting webhook at {WEBAPP_HOST}:{WEBAPP_PORT} -> {WEBHOOK_URL}")
        application.run_webhook(
            listen=WEBAPP_HOST,
            port=WEBAPP_PORT,
            webhook_url=WEBHOOK_URL,
        )
    else:
        # Long-polling mode (easy local dev)
        print("Starting long polling…")
        application.run_polling(close_loop=False)



