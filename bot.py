
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
        conn.execute(SCHEMA_SQL)
        conn.commit()


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
    "👋 Hi! You'll get one technical question per day (SQL, Algorithms, HTML).\n"
    "Use /subscribe or /unsubscribe. At any time: /today to resend today's question."
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
    # Ensure user exists and is subscribed
    with db() as conn:
        row = conn.execute(
            "SELECT chat_id, subscribed, last_question_id, last_sent_date FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()
    if not row:
        return
    chat_id, subscribed, last_qid, last_date = row
    if not subscribed:
        return

    # Avoid re-sending if already sent today via scheduler
    if last_date == today_str():
        # Just resend current question
        q = next((x for x in QUESTIONS if x["id"] == last_qid), None) or pick_question(last_qid)
    else:
        q = pick_question(last_qid)
        with db() as conn:
            conn.execute(
                "UPDATE users SET last_question_id=?, last_sent_date=? WHERE user_id=?",
                (q["id"], today_str(), user_id),
            )
            conn.commit()

    await send_question(chat_id, q, app)


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


def build_app() -> Application:
    if not TELEGRAM_TOKEN:
        raise SystemExit("Missing TELEGRAM_TOKEN env var")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("today", cmd_today))

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


