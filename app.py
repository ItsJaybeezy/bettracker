import os, sqlite3, requests, threading, time, base64
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from poller import (get_player_stat, get_team_score, get_injury_status,
                    resolve_player_bet_final, resolve_team_bet_final)

app = Flask(__name__)
DB_PATH = "bets.db"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# ── DB ───────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT DEFAULT '', stake REAL DEFAULT 0,
            status TEXT DEFAULT 'pending', created_at TEXT DEFAULT (datetime('now')))""")
        db.execute("""CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER, player_name TEXT NOT NULL,
            sport TEXT NOT NULL, stat_type TEXT NOT NULL,
            bet_type TEXT DEFAULT 'player_prop',
            target REAL NOT NULL, over_under TEXT DEFAULT 'over',
            stake REAL DEFAULT 0, odds TEXT DEFAULT '',
            game_date TEXT DEFAULT '', current_value REAL DEFAULT 0,
            status TEXT DEFAULT 'pending', last_alerted_milestone TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY(ticket_id) REFERENCES tickets(id))""")
        # Add bet_type column if upgrading from old DB
        try:
            db.execute("ALTER TABLE bets ADD COLUMN bet_type TEXT DEFAULT 'player_prop'")
        except: pass
        db.commit()

init_db()

# ── DISCORD ──────────────────────────────────────────────────────────
SPORT_EMOJIS = {"NBA":"🏀","NFL":"🏈","MLB":"⚾","NHL":"🏒","NCAAMB":"🏀","MLS":"⚽","SOCCER":"⚽","TENNIS":"🎾","MMA":"🥊","GOLF":"⛳","OTHER":"🎯"}

def progress_bar(current, target, length=10):
    pct = min(current/target,1.0) if target > 0 else 0
    filled = int(pct*length)
    return "["+"█"*filled+"░"*(length-filled)+f"] {int(pct*100)}%"

def send_discord(embed):
    if not DISCORD_WEBHOOK_URL: return
    try: requests.post(DISCORD_WEBHOOK_URL, json={"embeds":[embed]}, timeout=5)
    except Exception as e: print(f"[Discord] {e}")

def alert_bet(bet, event, prev=None):
    emoji = SPORT_EMOJIS.get(bet["sport"].upper(), "🎯")
    cur = float(bet["current_value"])
    tgt = float(bet["target"])
    stat = bet["stat_type"].replace("_"," ").title()
    bet_type = bet.get("bet_type","player_prop")
    ticket_label = bet.get("ticket_label","")

    # Team bet display
    if bet_type in ("moneyline","spread","game_total","team_prop"):
        player = bet["player_name"]
        if event == "update":
            if bet_type == "moneyline":
                winning = "✅ Leading" if cur > 0 else ("❌ Trailing" if cur < 0 else "🟡 Tied")
                title = f"{emoji} {player} — Score Update"
                desc = f"**Margin:** {cur:+.0f} ({winning})\n**Current Score:** {bet.get('score_display', '')}"
            elif bet_type == "spread":
                covering = "✅ Covering" if (bet["over_under"]=="over" and cur >= tgt) or (bet["over_under"]=="under" and cur <= tgt) else "❌ Not Covering"
                title = f"{emoji} {player} — Spread Update"
                desc = f"**Margin:** {cur:+.0f} vs Spread {tgt:+.1f}\n{covering}\n{progress_bar(abs(cur), abs(tgt))}"
            elif bet_type == "game_total":
                title = f"{emoji} Game Total Update"
                desc = f"**Combined Score:** {cur}\n**Target:** {bet['over_under'].upper()} {tgt}\n{progress_bar(cur, tgt)}"
            else:
                title = f"{emoji} {player} — Team Prop Update"
                desc = f"**Score:** {cur}\n**Target:** {bet['over_under'].upper()} {tgt}\n{progress_bar(cur, tgt)}"
            color = 0x3b82f6
        elif event == "hit":
            title = f"✅ {player} — BET HIT! 🎉"
            desc = f"**{stat}** settled in your favor!\n**Final:** {cur}"
            color = 0x22c55e
        elif event == "bust":
            title = f"❌ {player} — Bet Busted"
            desc = f"**{stat}** didn't go your way.\n**Final:** {cur}"
            color = 0xef4444
        else:
            return
        footer = f"PropTracker • {bet['sport']} • {ticket_label} • {datetime.now().strftime('%I:%M %p')}"
        send_discord({"title":title,"description":desc,"color":color,"footer":{"text":footer}})
        return

    # Player prop display
    if event == "update":
        delta = cur - (prev or 0)
        ds = f"+{delta:.1f}" if delta >= 0 else f"{delta:.1f}"
        title = f"{emoji} {bet['player_name']} — {stat} Update"
        desc = f"**Current:** {cur} ({ds})\n**Target:** {bet['over_under'].upper()} {tgt}\n{progress_bar(cur,tgt)}"
        color = 0x3b82f6
    elif event == "milestone_25":
        title = f"⚡ {bet['player_name']} — 25% of {tgt} {stat}"
        desc = f"{cur} / {tgt}\n{progress_bar(cur,tgt)}"
        color = 0xf59e0b
    elif event == "milestone_50":
        title = f"🔥 {bet['player_name']} — Halfway to {tgt} {stat}!"
        desc = f"{cur} / {tgt}\n{progress_bar(cur,tgt)}"
        color = 0xe67e22
    elif event == "milestone_75":
        title = f"💥 {bet['player_name']} — 75% of {tgt} {stat}!"
        desc = f"{cur} / {tgt} — Almost!\n{progress_bar(cur,tgt)}"
        color = 0xe74c3c
    elif event == "hit":
        title = f"✅ {bet['player_name']} — BET HIT! 🎉"
        desc = f"**{stat}:** {cur} ✓\n**Target:** {bet['over_under'].upper()} {tgt}\n{progress_bar(cur,tgt)}"
        color = 0x22c55e
    elif event == "bust":
        title = f"❌ {bet['player_name']} — Busted"
        desc = f"**{stat}:** {cur}\n**Target:** {bet['over_under'].upper()} {tgt}\n{progress_bar(cur,tgt)}"
        color = 0xef4444
    elif event == "injury":
        title = f"🚨 INJURY ALERT — {bet['player_name']}"
        desc = f"**Status:** {bet.get('injury_status','injured').upper()}\n**Sport:** {bet['sport']}\n⚠️ You have a live bet on this player!"
        color = 0xff6b00
    else:
        return

    footer = f"PropTracker • {bet['sport']} • {ticket_label} • {datetime.now().strftime('%I:%M %p')}"
    send_discord({"title":title,"description":desc,"color":color,"footer":{"text":footer}})

def alert_ticket(ticket, bets, status):
    hit = sum(1 for b in bets if b["status"]=="hit")
    total = len(bets)
    if status == "hit":
        title = f"🏆 {ticket['label']} — FULL TICKET HIT! 🎉"
        desc = f"All {total} props/bets hit!\n**Stake:** ${ticket['stake']}"
        color = 0x22c55e
    else:
        title = f"💀 {ticket['label']} — Ticket Busted"
        desc = f"{hit}/{total} hit\n**Stake:** ${ticket['stake']}"
        color = 0xef4444
    send_discord({"title":title,"description":desc,"color":color,
                  "footer":{"text":f"PropTracker • {datetime.now().strftime('%I:%M %p')}"}})

# ── BET LOGIC ────────────────────────────────────────────────────────
def check_milestones_and_status(bet, old_val, new_val):
    tgt = float(bet["target"])
    alerted = bet.get("last_alerted_milestone","") or ""
    new_status = bet["status"]
    bet_type = bet.get("bet_type","player_prop")

    if bet_type == "player_prop":
        for pct, event in [(0.25,"milestone_25"),(0.50,"milestone_50"),(0.75,"milestone_75")]:
            if old_val < tgt*pct <= new_val and event not in alerted:
                alert_bet(bet, event)
                alerted += f",{event}"

    if new_status not in ("hit","bust"):
        if bet["over_under"] == "over" and new_val >= tgt:
            new_status = "hit"
            alert_bet({**bet,"current_value":new_val}, "hit")
        elif bet["over_under"] == "under" and new_val <= tgt:
            new_status = "hit"
            alert_bet({**bet,"current_value":new_val}, "hit")

    return alerted.strip(","), new_status

def check_ticket_complete(ticket_id):
    db = get_db()
    bets = db.execute("SELECT * FROM bets WHERE ticket_id=?", (ticket_id,)).fetchall()
    ticket = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
    if not bets or not ticket: db.close(); return
    statuses = [b["status"] for b in bets]
    if all(s=="hit" for s in statuses):
        db.execute("UPDATE tickets SET status='hit' WHERE id=?", (ticket_id,))
        db.commit()
        alert_ticket(dict(ticket), [dict(b) for b in bets], "hit")
    elif "bust" in statuses and ticket["status"] not in ("hit","bust"):
        db.execute("UPDATE tickets SET status='bust' WHERE id=?", (ticket_id,))
        db.commit()
        alert_ticket(dict(ticket), [dict(b) for b in bets], "bust")
    db.close()

# ── AUTO POLLER ──────────────────────────────────────────────────────
def poll_loop():
    print("[Poller] Started — 15s interval, alerts on change only")
    while True:
        try:
            db = get_db()
            live_bets = db.execute("""
                SELECT b.*, t.label as ticket_label
                FROM bets b LEFT JOIN tickets t ON b.ticket_id=t.id
                WHERE b.status='live'
            """).fetchall()
            db.close()

            for bet in [dict(b) for b in live_bets]:
                bet_type = bet.get("bet_type","player_prop")

                # ── INJURY CHECK (player bets only) ──
                if bet_type == "player_prop":
                    changed, inj_status = get_injury_status(bet["sport"], bet["player_name"])
                    if changed and inj_status:
                        alert_bet({**bet,"injury_status":inj_status}, "injury")

                # ── FETCH CURRENT VALUE ──
                if bet_type == "player_prop":
                    new_val = get_player_stat(bet["sport"], bet["player_name"], bet["stat_type"])
                elif bet_type in ("moneyline","spread"):
                    data = get_team_score(bet["sport"], bet["player_name"])
                    if data is None:
                        # Game might be over — try final resolution
                        data = resolve_team_bet_final(bet)
                    if data:
                        new_val = data["margin"]
                        bet["score_display"] = f"{data['team_score']:.0f} - {data['opp_score']:.0f}"
                        # Auto-resolve if final
                        if data.get("is_final") and bet["status"] == "live":
                            final_status = "bust"
                            if bet_type == "moneyline":
                                final_status = "hit" if data["margin"] > 0 else "bust"
                            elif bet_type == "spread":
                                covers = data["margin"] >= bet["target"] if bet["over_under"]=="over" else data["margin"] <= bet["target"]
                                final_status = "hit" if covers else "bust"
                            alert_bet({**bet,"current_value":new_val}, final_status)
                            db = get_db()
                            db.execute("UPDATE bets SET status=?,current_value=?,updated_at=datetime('now') WHERE id=?",
                                       (final_status, new_val, bet["id"]))
                            db.commit(); db.close()
                            check_ticket_complete(bet["ticket_id"])
                            continue
                    else:
                        new_val = None
                elif bet_type == "game_total":
                    data = get_team_score(bet["sport"], bet["player_name"])
                    if data:
                        new_val = data["combined"]
                        if data.get("is_final") and bet["status"] == "live":
                            final_status = "hit" if (bet["over_under"]=="over" and new_val >= bet["target"]) or \
                                                    (bet["over_under"]=="under" and new_val <= bet["target"]) else "bust"
                            alert_bet({**bet,"current_value":new_val}, final_status)
                            db = get_db()
                            db.execute("UPDATE bets SET status=?,current_value=?,updated_at=datetime('now') WHERE id=?",
                                       (final_status, new_val, bet["id"]))
                            db.commit(); db.close()
                            check_ticket_complete(bet["ticket_id"])
                            continue
                    else:
                        new_val = None
                elif bet_type == "team_prop":
                    data = get_team_score(bet["sport"], bet["player_name"])
                    new_val = data["team_score"] if data else None
                else:
                    new_val = None

                if new_val is None: continue
                old_val = float(bet["current_value"])
                if new_val == old_val: continue

                print(f"[Poller] {bet['player_name']} {bet['stat_type']}: {old_val} → {new_val}")
                alert_bet({**bet,"current_value":new_val}, "update", prev=old_val)
                new_alerted, new_status = check_milestones_and_status(bet, old_val, new_val)

                db = get_db()
                db.execute("""UPDATE bets SET current_value=?,status=?,last_alerted_milestone=?,
                    updated_at=datetime('now') WHERE id=?""",
                    (new_val, new_status, new_alerted, bet["id"]))
                db.commit(); db.close()
                if bet.get("ticket_id"): check_ticket_complete(bet["ticket_id"])

        except Exception as e:
            print(f"[Poller] Error: {e}")
        time.sleep(15)

threading.Thread(target=poll_loop, daemon=True).start()

# ── TICKET SCANNER ───────────────────────────────────────────────────
@app.route("/scan_ticket", methods=["POST"])
def scan_ticket():
    if not ANTHROPIC_API_KEY:
        return jsonify({"error":"ANTHROPIC_API_KEY not set"}), 400
    file = request.files.get("image")
    if not file:
        return jsonify({"error":"No image"}), 400
    img_data = base64.b64encode(file.read()).decode("utf-8")
    media_type = file.content_type or "image/jpeg"
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1000,
        "messages": [{
            "role": "user",
            "content": [
                {"type":"image","source":{"type":"base64","media_type":media_type,"data":img_data}},
                {"type":"text","text":"""Extract every bet from this ticket. Return ONLY lines in this exact format, one per line, nothing else:
Player/Team Name | Sport | Stat/Bet Type | Over/Under | Target | Stake | Odds

Rules:
- For player props: stat type like Points, Rebounds, Passing Yards, Strikeouts etc.
- For moneylines: stat type = Moneyline, target = 0, over_under = over
- For spreads: stat type = Spread, target = the spread number (e.g. -7.5), over_under = over
- For game totals: use both team names like "Lakers vs Celtics", stat type = Game Total
- For team props: team name, stat type = Team Points or similar
- Sport should be NBA/NFL/MLB/NHL/NCAAMB/MLS/Other
- If stake or odds not visible, leave blank
- No headers, no explanation, just the lines"""}
            ]
        }]
    }
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"Content-Type":"application/json","x-api-key":ANTHROPIC_API_KEY},
            json=payload, timeout=30)
        result = r.json()
        text = result["content"][0]["text"].strip()
        return jsonify({"lines": text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── ROUTES ───────────────────────────────────────────────────────────
@app.route("/")
def index():
    db = get_db()
    tickets = db.execute("SELECT * FROM tickets ORDER BY created_at DESC").fetchall()
    tickets_data = []
    for t in tickets:
        bets = db.execute("SELECT * FROM bets WHERE ticket_id=? ORDER BY id",(t["id"],)).fetchall()
        tickets_data.append({"ticket":dict(t),"bets":[dict(b) for b in bets]})
    db.close()
    return render_template("index.html", tickets_data=tickets_data)

@app.route("/import", methods=["POST"])
def import_bets():
    data = request.json
    label = data.get("label", f"Ticket {datetime.now().strftime('%m/%d %I:%M%p')}")
    stake = float(data.get("stake",0))
    rows = data.get("rows",[])
    if not rows: return jsonify({"error":"No bets"}), 400
    db = get_db()
    cur = db.execute("INSERT INTO tickets (label,stake,status) VALUES (?,?,'pending')",(label,stake))
    ticket_id = cur.lastrowid
    for row in rows:
        stat = row.get("stat","").strip()
        bet_type = "player_prop"
        stat_lower = stat.lower()
        if stat_lower == "moneyline": bet_type = "moneyline"
        elif stat_lower == "spread": bet_type = "spread"
        elif stat_lower == "game total": bet_type = "game_total"
        elif "team" in stat_lower and "point" in stat_lower: bet_type = "team_prop"
        db.execute("""INSERT INTO bets
            (ticket_id,player_name,sport,stat_type,bet_type,target,over_under,stake,odds,game_date,status)
            VALUES (?,?,?,?,?,?,?,?,?,?,'pending')""",
            (ticket_id, row.get("player","").strip(), row.get("sport","Other").strip(),
             stat, bet_type, float(row.get("target",0)),
             row.get("over_under","over").lower().strip(),
             float(row.get("stake",0)), row.get("odds","").strip(), row.get("date","").strip()))
    db.commit(); db.close()
    return jsonify({"success":True,"ticket_id":ticket_id})

@app.route("/update_bet/<int:bet_id>", methods=["POST"])
def update_bet(bet_id):
    data = request.json
    db = get_db()
    bet = db.execute("SELECT b.*,t.label as ticket_label FROM bets b LEFT JOIN tickets t ON b.ticket_id=t.id WHERE b.id=?",(bet_id,)).fetchone()
    if not bet: return jsonify({"error":"Not found"}), 404
    bet = dict(bet)
    new_val = float(data.get("current_value", bet["current_value"]))
    new_status = data.get("status", bet["status"])
    old_val = float(bet["current_value"])
    if new_val != old_val:
        alert_bet({**bet,"current_value":new_val}, "update", prev=old_val)
        new_alerted, auto_status = check_milestones_and_status(bet, old_val, new_val)
        if new_status not in ("hit","bust"): new_status = auto_status
    else:
        new_alerted = bet["last_alerted_milestone"]
    if new_status in ("hit","bust") and bet["status"] not in ("hit","bust"):
        alert_bet({**bet,"current_value":new_val}, new_status)
    db.execute("UPDATE bets SET current_value=?,status=?,last_alerted_milestone=?,updated_at=datetime('now') WHERE id=?",
               (new_val, new_status, new_alerted, bet_id))
    db.commit(); db.close()
    if bet.get("ticket_id"): check_ticket_complete(bet["ticket_id"])
    return jsonify({"success":True})

@app.route("/delete_ticket/<int:ticket_id>", methods=["POST"])
def delete_ticket(ticket_id):
    db = get_db()
    db.execute("DELETE FROM bets WHERE ticket_id=?", (ticket_id,))
    db.execute("DELETE FROM tickets WHERE id=?", (ticket_id,))
    db.commit(); db.close()
    return jsonify({"success":True})

@app.route("/set_live/<int:ticket_id>", methods=["POST"])
def set_live(ticket_id):
    db = get_db()
    db.execute("UPDATE bets SET status='live' WHERE ticket_id=? AND status='pending'",(ticket_id,))
    db.execute("UPDATE tickets SET status='live' WHERE id=?",(ticket_id,))
    db.commit(); db.close()
    return jsonify({"success":True})

@app.route("/settings")
def settings():
    return render_template("settings.html",
        discord_set=bool(DISCORD_WEBHOOK_URL),
        anthropic_set=bool(ANTHROPIC_API_KEY))

if __name__ == "__main__":
    port = int(os.environ.get("PORT",5000))
    app.run(host="0.0.0.0", port=port, debug=False)
