"""Collect high-rank Epic Seven RTA battles from the official match-history API.

Starts from one player, saves their last 100 battles, then follows their
opponents (highest rank first). Each battle is stored once, even when both
players get crawled.

Usage:
    python crawl.py                     # default seed, up to 250 players
    python crawl.py --max-players 500
    python crawl.py --seed 123456789:world_global
"""
import argparse
import heapq
import json
import sqlite3
import time
import urllib.request
from pathlib import Path

API = "https://e7api.onstove.com/gameApi/getBattleList?nick_no={nick}&world_code={world}&lang=en"
HERO_JSON = "https://static-pubcomm.onstove.com/gameRecord/epic7/epic7_hero.json"  # the one the official site uses
GRADES = ["bronze", "silver", "gold", "master", "challenger", "champion", "warlord", "emperor", "legend"]
GRADE_RANK = {g: i for i, g in enumerate(GRADES)}
DEFAULT_SEED = "199070445:world_asia"  # public account from the e7-rta-history project
HERE = Path(__file__).parent
HEADERS = {"Accept": "application/json", "User-Agent": "e7-draft-helper/0.1 (personal use)"}


def fetch(url, method="GET", attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, method=method, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(3 * attempt)


def parse_team(fragment):
    """teamBettleInfo looks like '"my_team":[...]' - wrap it into real JSON."""
    try:
        return json.loads("{" + fragment + "}")["my_team"]
    except Exception:
        return []


def side(deck, team_info):
    """Return one team's draft: picks in pick order, the post-banned hero, prebans, first pick."""
    heroes = deck.get("hero_list") or []
    order = {h["hero_code"]: h.get("pick_order", 99) for h in parse_team(team_info)}
    picks = sorted(heroes, key=lambda h: order.get(h["hero_code"], 99))
    return {
        "picks": [h["hero_code"] for h in picks],
        "banned": next((h["hero_code"] for h in heroes if h.get("ban") == 1), None),
        "prebans": deck.get("preban_list") or [],
        "first": any(h.get("first_pick") == 1 for h in heroes),
    }


def open_db(path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS players (
            nick INTEGER, world TEXT, grade TEXT, fetched_at REAL, status TEXT,
            PRIMARY KEY (nick, world));
        CREATE TABLE IF NOT EXISTS battles (
            key TEXT PRIMARY KEY, season TEXT, day TEXT,
            a_nick INTEGER, a_world TEXT, a_grade TEXT, a_win INTEGER, a_draft TEXT,
            b_nick INTEGER, b_world TEXT, b_grade TEXT, b_draft TEXT);
    """)
    return db


def save_battles(db, battles):
    new = 0
    for b in battles:
        a_nick, b_nick = int(b["nicknameno"]), int(b["matchPlayerNicknameno"])
        # The same battle shows up in both players' histories; key it by the pair + time.
        key = f"{min(a_nick, b_nick)}-{max(a_nick, b_nick)}-{b['battle_day']}"
        row = (key, b["season_code"], b["battle_day"],
               a_nick, b["worldCode"], b["grade_code"], int(b["iswin"] == 1),
               json.dumps(side(b["my_deck"], b.get("teamBettleInfo", ""))),
               b_nick, b["enemy_world_code"], b["enemy_grade_code"],
               json.dumps(side(b["enemy_deck"], b.get("teamBettleInfoenemy", ""))))
        cur = db.execute("INSERT OR IGNORE INTO battles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
        new += cur.rowcount
    return new


def crawl(db, seed, max_players, min_grade, delay, refetch_hours):
    min_rank = GRADE_RANK[min_grade]
    seed_nick, seed_world = seed.split(":")
    queue = [(-99, int(seed_nick), seed_world)]  # (negative rank, nick, world)
    queued = {(int(seed_nick), seed_world)}
    done = 0
    cutoff = time.time() - refetch_hours * 3600

    while queue and done < max_players:
        _, nick, world = heapq.heappop(queue)
        row = db.execute("SELECT fetched_at FROM players WHERE nick=? AND world=?", (nick, world)).fetchone()
        fresh = row and row[0] and row[0] > cutoff
        if fresh:
            # Already fetched recently: reuse its stored opponents instead of calling the API again.
            battles = None
        else:
            try:
                raw = fetch(API.format(nick=nick, world=world), method="POST")
                data = json.loads(raw.decode("utf-8-sig"))
                battles = data["value"]["result_body"]["battle_list"] or []
                status = "ok"
            except Exception as e:
                battles, status = [], f"error: {e}"
            grade = battles[0]["grade_code"] if battles else None
            new = save_battles(db, battles)
            db.execute("INSERT OR REPLACE INTO players VALUES (?,?,?,?,?)", (nick, world, grade, time.time(), status))
            db.commit()
            done += 1
            print(f"[{done}/{max_players}] {nick}@{world} {grade or '-'}: {len(battles)} battles, {new} new ({status})")
            time.sleep(delay)

        # Queue this player's opponents, highest rank first.
        opponents = db.execute(
            "SELECT b_nick, b_world, b_grade FROM battles WHERE a_nick=? UNION "
            "SELECT a_nick, a_world, a_grade FROM battles WHERE b_nick=?", (nick, nick)).fetchall()
        for o_nick, o_world, o_grade in opponents:
            rank = GRADE_RANK.get(o_grade, -1)
            if rank >= min_rank and (o_nick, o_world) not in queued:
                queued.add((o_nick, o_world))
                heapq.heappush(queue, (-rank, o_nick, o_world))

    total = db.execute("SELECT COUNT(*) FROM battles").fetchone()[0]
    print(f"Done. Fetched {done} players; {total} unique battles stored.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed", default=DEFAULT_SEED, help="starting player as nick_no:world_code")
    p.add_argument("--max-players", type=int, default=250)
    p.add_argument("--min-grade", default="champion", choices=GRADES, help="only follow opponents at or above this rank")
    p.add_argument("--delay", type=float, default=1.2, help="seconds between API calls")
    p.add_argument("--refetch-hours", type=float, default=12, help="skip players fetched more recently than this")
    p.add_argument("--db", default=str(HERE / "e7.db"))
    args = p.parse_args()

    heroes = HERE / "heroes.json"
    heroes.write_bytes(fetch(HERO_JSON))  # official hero names, incl. zh-CN
    db = open_db(args.db)
    crawl(db, args.seed, args.max_players, args.min_grade, args.delay, args.refetch_hours)


if __name__ == "__main__":
    main()
