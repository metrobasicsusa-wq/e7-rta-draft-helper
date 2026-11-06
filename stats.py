"""Turn the crawled battles into draft stats and build the draft-helper page.

Usage:
    python stats.py                 # current season, both players Champion+
    python stats.py --min-grade warlord
Writes stats.json and draft.html next to this file.
"""
import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).parent
GRADES = ["bronze", "silver", "gold", "master", "challenger", "champion", "warlord", "emperor", "legend"]


def load_battles(db_path, min_grade, season):
    db = sqlite3.connect(db_path)
    if not season:
        season = db.execute("SELECT season FROM battles GROUP BY season ORDER BY MAX(day) DESC LIMIT 1").fetchone()[0]
    ok = set(GRADES[GRADES.index(min_grade):])
    rows = db.execute("SELECT a_grade, b_grade, a_win, a_draft, b_draft, day FROM battles WHERE season=?", (season,))
    battles = []
    days = []
    for a_grade, b_grade, a_win, a_draft, b_draft, day in rows:
        if a_grade in ok and b_grade in ok:
            a, b = json.loads(a_draft), json.loads(b_draft)
            if len(a["picks"]) == 5 and len(b["picks"]) == 5:
                battles.append((a, b, a_win))
                days.append(day)
    return season, battles, (min(days)[:10], max(days)[:10]) if days else (None, None)


def compute(battles, min_pair):
    hero = defaultdict(Counter)
    pair = defaultdict(lambda: [0, 0])     # (h1, h2) same team -> [games, wins]
    vs = defaultdict(lambda: [0, 0])       # (h, e) h's team vs e's team -> [games, wins for h]

    for a, b, a_win in battles:
        for prebanned in set(a["prebans"]) | set(b["prebans"]):
            hero[prebanned]["preban"] += 1
        sides = [(a, a_win, b), (b, 1 - a_win, a)]
        played = {id(a): [h for h in a["picks"] if h != a["banned"]],
                  id(b): [h for h in b["picks"] if h != b["banned"]]}
        for me, win, foe in sides:
            for slot, h in enumerate(me["picks"]):
                c = hero[h]
                c["picked"] += 1
                if h == me["banned"]:
                    c["postban"] += 1
                else:
                    c["played"] += 1
                    c["wins"] += win
                if slot == 0 and me["first"]:
                    c["fp"] += 1
                    c["fp_wins"] += win
            mine, theirs = played[id(me)], played[id(foe)]
            for h1, h2 in combinations(sorted(mine), 2):
                p = pair[(h1, h2)]
                p[0] += 1
                p[1] += win
            for h in mine:
                for e in theirs:
                    if h < e:
                        v = vs[(h, e)]
                        v[0] += 1
                        v[1] += win
    return hero, pair, vs


def build(season, battles, span, hero, pair, vs, min_games, min_pair):
    names = json.loads((HERE / "heroes.json").read_text(encoding="utf-8-sig"))
    info = {}
    for lang in ("zh-CN", "en"):
        for h in names.get(lang, []):
            info.setdefault(h["code"], {"job": h.get("job_cd"), "el": h.get("attribute_cd")})[lang] = h["name"]

    n = len(battles)
    codes = sorted((h for h, c in hero.items() if c["played"] >= min_games or c["preban"] >= min_games),
                   key=lambda h: -(hero[h]["picked"] + hero[h]["preban"]))
    idx = {h: i for i, h in enumerate(codes)}
    heroes = []
    for h in codes:
        c, meta = hero[h], info.get(h, {})
        heroes.append({
            "code": h, "zh": meta.get("zh-CN") or meta.get("en") or h, "en": meta.get("en") or h,
            "el": meta.get("el"), "job": meta.get("job"),
            "picked": c["picked"], "played": c["played"], "wins": c["wins"],
            "postban": c["postban"], "preban": c["preban"], "fp": c["fp"], "fpWins": c["fp_wins"],
        })
    pairs = [[idx[a], idx[b], g, w] for (a, b), (g, w) in pair.items() if g >= min_pair and a in idx and b in idx]
    matchups = [[idx[a], idx[b], g, w] for (a, b), (g, w) in vs.items() if g >= min_pair and a in idx and b in idx]
    return {"season": season, "battles": n, "from": span[0], "to": span[1],
            "heroes": heroes, "pairs": pairs, "vs": matchups}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(HERE / "e7.db"))
    p.add_argument("--min-grade", default="champion", choices=GRADES)
    p.add_argument("--season", default=None, help="season code, default: latest")
    p.add_argument("--min-games", type=int, default=30, help="keep heroes with at least this many games")
    p.add_argument("--min-pair", type=int, default=15, help="keep pairs/matchups with at least this many games")
    args = p.parse_args()

    season, battles, span = load_battles(args.db, args.min_grade, args.season)
    hero, pair, vs = compute(battles, args.min_pair)
    stats = build(season, battles, span, hero, pair, vs, args.min_games, args.min_pair)
    stats["minGrade"] = args.min_grade
    blob = json.dumps(stats, ensure_ascii=False, separators=(",", ":"))
    (HERE / "stats.json").write_text(blob, encoding="utf-8")

    template = (HERE / "template.html").read_text(encoding="utf-8")
    page = template.replace("/*__STATS__*/null", blob.replace("</", "<\\/"))
    (HERE / "draft.html").write_text(page, encoding="utf-8")
    print(f"{season}: {len(battles)} battles ({span[0]} to {span[1]}), {len(stats['heroes'])} heroes, "
          f"{len(stats['pairs'])} pairs, {len(stats['vs'])} matchups. Page: {len(page) // 1024} KB")


if __name__ == "__main__":
    main()
