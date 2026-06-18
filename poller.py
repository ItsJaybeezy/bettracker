import requests
from datetime import datetime

ESPN_SCOREBOARD = {
    "NBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "NFL": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "MLB": "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
    "NHL": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard",
    "NCAAMB": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",
    "MLS": "https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard",
}
ESPN_SUMMARY = {
    "NBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary",
    "NFL": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary",
    "MLB": "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary",
    "NHL": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary",
    "NCAAMB": "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary",
    "MLS": "https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/summary",
}

STAT_MAP = {
    "points": ["PTS","points"],
    "rebounds": ["REB","rebounds","totalRebounds"],
    "assists": ["AST","assists"],
    "blocks": ["BLK","blocks"],
    "steals": ["STL","steals"],
    "three pointers made": ["3PM","threePointFieldGoalsMade"],
    "three-pointers made": ["3PM","threePointFieldGoalsMade"],
    "turnovers": ["TO","turnovers"],
    "passing yards": ["passingYards","passYards","YDS"],
    "passing touchdowns": ["passingTouchdowns","passTDs"],
    "rushing yards": ["rushingYards","rushYards"],
    "receiving yards": ["receivingYards","recYards"],
    "receptions": ["receptions","REC"],
    "touchdowns": ["touchdowns","TD","TDs"],
    "strikeouts": ["strikeouts","K","SO"],
    "hits": ["hits","H"],
    "home runs": ["homeRuns","HR"],
    "rbi": ["RBI","rbi"],
    "earned runs": ["earnedRuns","ER"],
    "walks": ["walks","BB"],
    "goals": ["goals","G"],
    "saves": ["saves","SV"],
    "shots": ["shots","SOG"],
}

def norm(s):
    return s.lower().replace("_"," ").strip()

def get_scoreboard(sport):
    url = ESPN_SCOREBOARD.get(sport.upper())
    if not url: return []
    try:
        r = requests.get(url, timeout=8)
        return r.json().get("events", [])
    except:
        return []

def get_live_games(sport):
    return [e for e in get_scoreboard(sport)
            if e.get("status",{}).get("type",{}).get("state","") == "in"]

def get_finished_games(sport):
    return [e for e in get_scoreboard(sport)
            if e.get("status",{}).get("type",{}).get("state","") == "post"]

def get_summary(sport, game_id):
    url = ESPN_SUMMARY.get(sport.upper())
    if not url: return {}
    try:
        r = requests.get(url, params={"event": game_id}, timeout=8)
        return r.json()
    except:
        return {}

# ── PLAYER STATS ─────────────────────────────────────────────────────
def get_player_stat(sport, player_name, stat_type, game_ids=None):
    sport = sport.upper()
    if game_ids is None:
        game_ids = [e["id"] for e in get_live_games(sport)]
    if not game_ids:
        return None
    player_lower = player_name.lower().strip()
    stat_keys = STAT_MAP.get(norm(stat_type), [norm(stat_type)])
    for game_id in game_ids:
        data = get_summary(sport, game_id)
        boxscore = data.get("boxscore", {})
        for team in boxscore.get("players", []):
            for sg in team.get("statistics", []):
                keys = sg.get("keys", []) + sg.get("labels", [])
                stat_idx = next((i for sk in stat_keys for i,k in enumerate(keys) if sk.lower()==k.lower()), None)
                if stat_idx is None: continue
                for athlete in sg.get("athletes", []):
                    name = athlete.get("athlete",{}).get("displayName","").lower()
                    short = athlete.get("athlete",{}).get("shortName","").lower()
                    if player_lower in name or player_lower in short or name in player_lower:
                        stats = athlete.get("stats", [])
                        if stat_idx < len(stats):
                            try:
                                val = stats[stat_idx]
                                if isinstance(val, str) and "-" in val:
                                    val = val.split("-")[0]
                                return float(val)
                            except: pass
    return None

# ── TEAM SCORES ───────────────────────────────────────────────────────
def get_team_score(sport, team_name):
    """Returns (team_score, opponent_score, game_id, is_live, is_final) or None"""
    sport = sport.upper()
    events = get_scoreboard(sport)
    team_lower = team_name.lower().strip()
    for event in events:
        state = event.get("status",{}).get("type",{}).get("state","")
        if state not in ("in","post"): continue
        comps = event.get("competitions",[{}])[0]
        competitors = comps.get("competitors", [])
        for comp in competitors:
            name = comp.get("team",{}).get("displayName","").lower()
            short = comp.get("team",{}).get("shortDisplayName","").lower()
            abbr = comp.get("team",{}).get("abbreviation","").lower()
            if team_lower in name or team_lower in short or team_lower == abbr or name in team_lower:
                my_score = float(comp.get("score", 0) or 0)
                opp = next((c for c in competitors if c != comp), {})
                opp_score = float(opp.get("score", 0) or 0)
                return {
                    "team_score": my_score,
                    "opp_score": opp_score,
                    "combined": my_score + opp_score,
                    "margin": my_score - opp_score,
                    "game_id": event["id"],
                    "is_live": state == "in",
                    "is_final": state == "post",
                    "status_detail": event.get("status",{}).get("type",{}).get("shortDetail",""),
                }
    return None

# ── INJURY CHECK ──────────────────────────────────────────────────────
_injury_cache = {}  # player_name -> last known status

def check_player_injury(sport, player_name):
    """Returns injury status string if player is injured/out, else None"""
    sport = sport.upper()
    events = get_scoreboard(sport)
    player_lower = player_name.lower().strip()
    for event in events:
        comps = event.get("competitions",[{}])[0]
        for comp in comps.get("competitors",[]):
            roster = comp.get("roster",[])
            for p in roster:
                name = p.get("athlete",{}).get("displayName","").lower()
                if player_lower in name or name in player_lower:
                    status = p.get("athlete",{}).get("status","active").lower()
                    if status not in ("active",""):
                        return status
    # Also check injuries endpoint
    try:
        sport_path = {
            "NBA":"basketball/nba","NFL":"football/nfl",
            "MLB":"baseball/mlb","NHL":"hockey/nhl"
        }.get(sport)
        if not sport_path: return None
        r = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/injuries", timeout=6)
        for team in r.json().get("injuries",[]):
            for inj in team.get("injuries",[]):
                name = inj.get("athlete",{}).get("displayName","").lower()
                if player_lower in name or name in player_lower:
                    status = inj.get("status","").lower()
                    details = inj.get("details",{}).get("type","")
                    return f"{status} — {details}" if details else status
    except: pass
    return None

def get_injury_status(sport, player_name):
    """Returns (changed, new_status) — only True if status worsened"""
    key = f"{sport}:{player_name.lower()}"
    current = check_player_injury(sport, player_name)
    prev = _injury_cache.get(key)
    if current and current != prev:
        _injury_cache[key] = current
        return True, current
    if not current and prev:
        _injury_cache[key] = None
    return False, current

# ── GAME FINAL RESOLUTION ─────────────────────────────────────────────
def resolve_player_bet_final(bet):
    """Get final stat for a player bet after game ends"""
    sport = bet["sport"].upper()
    finished = [e["id"] for e in get_finished_games(sport)]
    if not finished: return None
    return get_player_stat(sport, bet["player_name"], bet["stat_type"], game_ids=finished)

def resolve_team_bet_final(bet):
    """Get final score data for a team bet after game ends"""
    return get_team_score(bet["sport"], bet["player_name"])
