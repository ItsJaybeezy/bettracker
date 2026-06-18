import os
import sqlite3
import requests
import threading
import time
import json
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for

app = Flask(__name__)
DB_PATH = "bets.db"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
SPORTRADAR_API_KEY = os.environ.get("SPORTRADAR_API_KEY", "")

# ──────────────────────────────────────────────
# DATABASE
# ──────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_name TEXT NOT NULL,
                sport TEXT NOT NULL,
                stat_type TEXT NOT NULL,
                target REAL NOT NULL,
                over_under TEXT NOT NULL DEFAULT 'over',
                stake REAL DEFAULT 0,
                odds TEXT DEFAULT '',
                game_date TEXT,
                current_value REAL DEFAULT 0,
                status TEXT DEFAULT 'pending',
                last_alerted_milestone TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        db.commit()

# ──────────────────────────────────────────────
# DISCORD
# ──────────────────────────────────────────────

SPORT_EMOJIS = {
    "NBA": "🏀", "NFL": "🏈", "MLB": "⚾", "NHL": "🏒"
}

STAT_UNITS = {
    "points": "pts", "rebounds": "reb", "assists": "ast",
    "passing_yards": "yds", "rushing_yards": "yds", "receiving_yards": "yds",
    "touchdowns": "TDs", "strikeouts": "K", "hits": "H", "home_runs": "HR",
    "rbi": "RBI", "goals": "G", "saves": "SV", "blocks": "BLK", "steals": "STL"
}

def progress_bar(current, target, length=10):
    pct = min(current / target, 1.0) if target > 0 else 0
    filled = int(pct * length)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {int(pct*100)}%"

def send_discord_alert(bet, event_type, previous_value=None):
    if not DISCORD_WEBHOOK_URL:
        print("[Discord] No webhook URL set, skipping alert.")
        return

    emoji = SPORT_EMOJIS.get(bet["sport"], "🎯")
    unit = STAT_UNITS.get(bet["stat_type"], bet["stat_type"])
    current = bet["current_value"]
    target = bet["target"]
    pct = (current / target * 100) if target > 0 else 0

    if event_type == "update":
        delta = current - (previous_value or 0)
        delta_str = f"+{delta:.1f}" if delta > 0 else f"{delta:.1f}"
        title = f"{emoji} **{bet['player_name']}** — Stat Update"
        desc = (
            f"**{bet['stat_type'].replace('_',' ').title()}:** {current} {unit} ({delta_str})\n"
            f"**Target:** {bet['over_under'].upper()} {target} {unit}\n"
            f"{progress_bar(current, target)}\n"
        )
        color = 0x3498db

    elif event_type == "milestone_25":
        title = f"⚡ **{bet['player_name']}** — 25% There!"
        desc = f"{current} / {target} {unit} — Keep going!\n{progress_bar(current, target)}"
        color = 0xf39c12

    elif event_type == "milestone_50":
        title = f"🔥 **{bet['player_name']}** — Halfway!"
        desc = f"{current} / {target} {unit}\n{progress_bar(current, target)}"
        color = 0xe67e22

    elif event_type == "milestone_75":
        title = f"💥 **{bet['player_name']}** — 75% Done!"
        desc = f"{current} / {target} {unit} — Almost there!\n{progress_bar(current, target)}"
        color = 0xe74c3c

    elif event_type == "hit":
        title = f"✅ **{bet['player_name']}** — BET HIT! 🎉"
        desc = (
            f"**{bet['stat_type'].replace('_',' ').title()}:** {current} {unit} ✓\n"
            f"**Target was:** {bet['over_under'].upper()} {target} {unit}\n"
            f"**Stake:** ${bet['stake']} @ {bet['odds']}\n"
            f"{progress_bar(current, target)}"
        )
        color = 0x2ecc71

    elif event_type == "bust":
        title = f"❌ **{bet['player_name']}** — Bet Busted"
        desc = (
            f"**{bet['stat_type'].replace('_',' ').title()}:** {current} {unit}\n"
            f"**Target was:** {bet['over_under'].upper()} {target} {unit}\n"
            f"**Stake:** ${bet['stake']}\n"
            f"{progress_bar(current, target)}"
        )
        color = 0xe74c3c

    else:
        return

    payload = {
        "embeds": [{
            "title": title,
            "description": desc,
            "color": color,
            "footer": {"text": f"PropTracker • {bet['sport']} • {datetime.now().strftime('%I:%M %p')}"}
        }]
    }

    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=5)
        r.raise_for_status()
    except Exception as e:
        print(f"[Discord] Failed to send alert: {e}")

# ──────────────────────────────────────────────
# SPORTRADAR STAT FETCHING
# ──────────────────────────────────────────────

def fetch_live_stat(player_name, sport, stat_type):
    """
    Fetch live player stat from Sportradar.
    Returns float or None if unavailable.
    """
    if not SPORTRADAR_API_KEY:
        return None

    sport = sport.upper()
    player_lower = player_name.lower().replace(" ", "%20")

    try:
        if sport == "NBA":
            url = f"https://api.sportradar.us/nba/trial/v8/en/games/day/boxscore.json?api_key={SPORTRADAR_API_KEY}"
            # In production: search today's games, find player, return stat
            # This is the real endpoint pattern — swap trial/v8 for your tier
            pass

        elif sport == "NFL":
            url = f"https://api.sportradar.us/nfl/official/trial/v7/en/games/day/boxscore.json?api_key={SPORTRADAR_API_KEY}"
            pass

        elif sport == "MLB":
            url = f"https://api.sportradar.us/mlb/trial/v7/en/games/day/boxscore.json?api_key={SPORTRADAR_API_KEY}"
            pass

        elif sport == "NHL":
            url = f"https://api.sportradar.us/nhl/trial/v7/en/games/day/boxscore.json?api_key={SPORTRADAR_API_KEY}"
            pass

    except Exception as e:
        print(f"[Sportradar] Error fetching {player_name}: {e}")

    return None  # Replace with parsed stat value

# ──────────────────────────────────────────────
# LIVE POLLING ENGINE
# ──────────────────────────────────────────────

def check_milestones(bet, old_val, new_val):
    """Fire milestone alerts when crossing 25/50/75% thresholds."""
    target = bet["target"]
    if target <= 0:
        return

    milestones = [
        (0.25, "milestone_25"),
        (0.50, "milestone_50"),
        (0.75, "milestone_75"),
    ]

    alerted = bet["last_alerted_milestone"] or ""

    for pct, event in milestones:
        threshold = target * pct
        if old_val < threshold <= new_val and event not in alerted:
            send_discord_alert(dict(bet), event)
            alerted += f",{event}"

    return alerted.strip(",")

def poll_live_stats():
    """Background thread: polls Sportradar every 60s for active bets."""
    while True:
        try:
            db = get_db()
            active_bets = db.execute(
                "SELECT * FROM bets WHERE status = 'live'"
            ).fetchall()

            for bet in active_bets:
                new_val = fetch_live_stat(bet["player_name"], bet["sport"], bet["stat_type"])

                if new_val is None:
                    continue

                old_val = bet["current_value"]

                if new_val != old_val:
                    # Always send update alert
                    send_discord_alert(dict(bet) | {"current_value": new_val}, "update", previous_value=old_val)

                    # Check milestones
                    new_alerted = check_milestones(bet, old_val, new_val)

                    # Check hit/bust
                    new_status = bet["status"]
                    if bet["over_under"] == "over" and new_val >= bet["target"]:
                        new_status = "hit"
                        send_discord_alert(dict(bet) | {"current_value": new_val}, "hit")
                    elif bet["over_under"] == "under" and new_val <= bet["target"]:
                        new_status = "hit"
                        send_discord_alert(dict(bet) | {"current_value": new_val}, "hit")

                    db.execute("""
                        UPDATE bets SET
                            current_value = ?,
                            status = ?,
                            last_alerted_milestone = ?,
                            updated_at = datetime('now')
                        WHERE id = ?
                    """, (new_val, new_status, new_alerted, bet["id"]))
                    db.commit()

            db.close()
        except Exception as e:
            print(f"[Poller] Error: {e}")

        time.sleep(60)

# ──────────────────────────────────────────────
# ROUTES
# ──────────────────────────────────────────────

@app.route("/")
def index():
    db = get_db()
    bets = db.execute("SELECT * FROM bets ORDER BY created_at DESC").fetchall()
    db.close()
    return render_template("index.html", bets=bets)

@app.route("/add", methods=["POST"])
def add_bet():
    data = request.form
    db = get_db()
    db.execute("""
        INSERT INTO bets (player_name, sport, stat_type, target, over_under, stake, odds, game_date, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (
        data["player_name"], data["sport"], data["stat_type"],
        float(data["target"]), data["over_under"],
        float(data.get("stake", 0)), data.get("odds", ""),
        data.get("game_date", "")
    ))
    db.commit()
    db.close()
    return redirect(url_for("index"))

@app.route("/update_status/<int:bet_id>", methods=["POST"])
def update_status(bet_id):
    data = request.json
    db = get_db()
    bet = db.execute("SELECT * FROM bets WHERE id = ?", (bet_id,)).fetchone()
    if not bet:
        return jsonify({"error": "Not found"}), 404

    new_status = data.get("status", bet["status"])
    new_val = data.get("current_value", bet["current_value"])
    old_val = bet["current_value"]

    if new_val != old_val:
        send_discord_alert(dict(bet) | {"current_value": new_val}, "update", previous_value=old_val)
        new_alerted = check_milestones(bet, old_val, new_val)

        if new_status in ("hit", "bust"):
            send_discord_alert(dict(bet) | {"current_value": new_val}, new_status)
    else:
        new_alerted = bet["last_alerted_milestone"]

    db.execute("""
        UPDATE bets SET status=?, current_value=?, last_alerted_milestone=?, updated_at=datetime('now')
        WHERE id=?
    """, (new_status, new_val, new_alerted, bet_id))
    db.commit()
    db.close()
    return jsonify({"success": True})

@app.route("/delete/<int:bet_id>", methods=["POST"])
def delete_bet(bet_id):
    db = get_db()
    db.execute("DELETE FROM bets WHERE id = ?", (bet_id,))
    db.commit()
    db.close()
    return jsonify({"success": True})

@app.route("/api/bets")
def api_bets():
    db = get_db()
    bets = db.execute("SELECT * FROM bets ORDER BY created_at DESC").fetchall()
    db.close()
    return jsonify([dict(b) for b in bets])

@app.route("/settings")
def settings():
    return render_template("settings.html",
        discord_set=bool(DISCORD_WEBHOOK_URL),
        sportradar_set=bool(SPORTRADAR_API_KEY)
    )

# ──────────────────────────────────────────────
# STARTUP
# ──────────────────────────────────────────────

init_db()

if __name__ == "__main__":
    # Start background polling thread
    poller = threading.Thread(target=poll_live_stats, daemon=True)
    poller.start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
