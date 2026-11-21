"""
Статистика забегов: разбор логов бота в список матчей.

Отдельный источник данных не заводится нарочно. Лог бот пишет всегда,
он уже лежит рядом за все прошлые прогоны, и держать параллельно второй
файл с теми же числами значило бы следить, чтобы они не разъехались.

Бот пишет по матчу строку «ИТОГ МАТЧА», в которой есть всё сразу. Старые
логи её не знают, поэтому для них матч собирается из отдельных отметок —
волн, смертей, нажатий BKB. Оба пути дают одинаковый набор полей.

    py stats.py            сводка по всем логам
    py stats.py bot.log    по одному файлу
"""

import glob
import json
import os
import re
import sys
import time

import core

HERE = core.HERE

TIME = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] (.*)$")

TOTAL = re.compile(
    r"ИТОГ МАТЧА: длительность (\d+):(\d\d), волн (\d+), боссов (\d+), "
    r"BKB (\d+) \(пропущено по откату (\d+)\), смертей (\d+), сатаник (\d+), "
    r"талантов (\d+) из (\d+), предметов (\d+) из (\d+), уровень (\d+), "
    r"конец: (.*)$")

OLD_END = re.compile(r"за матч волн: (\d+), из них боссов: (\d+)")
WAVE = re.compile(r"^ВОЛНА (\d+): (?!сменилась|зачищена)")
BKB_HIT = re.compile(r"^BKB нажат \((\d+)-й раз\)")
BKB_SKIP = re.compile(r"^BKB на волне \d+: в откате")
BOUGHT = re.compile(r"^(?:КУПЛЕНО (\d+) из (\d+)|куплено (\d+)/(\d+))")
STATE = re.compile(r"^состояние: уровень (\d+), талантов (\d+) из (\d+)")
TALENTS_ALL = re.compile(r"^план талантов выполнен целиком: (\d+) из (\d+)")
ENDED = re.compile(r"^матч закончился(?:: (.*))?$")
PICKED = re.compile(r"^беру (.+?)\s{2,}\(сейчас состояние")
SWEEP = re.compile(r"^обход: беру (\S+?),")
SWITCH = re.compile(r"^настройки сменились: билд \S+ -> (\S+)")

FIELDS = ("secs", "waves", "bosses", "bkb", "bkb_skipped", "deaths",
          "satanic", "talents", "talents_plan", "items", "items_plan",
          "level")


def blank(start):
    rec = {f: 0 for f in FIELDS}
    rec.update(start=start, end=start, why="", file="", key="", over=False,
               hero="")
    return rec


def worth_keeping(rec):
    return bool(rec["waves"] or rec["over"] or rec["end"] - rec["start"] >= 60)


def finish(rec):
    rec["secs"] = rec["secs"] or round(rec["end"] - rec["start"])
    rec["why"] = rec["why"] or "оборван"
    return rec


def stamps(lines, mtime):
    raw = []
    for ln in lines:
        m = TIME.match(ln)
        raw.append((int(m.group(1)) * 3600 + int(m.group(2)) * 60
                    + int(m.group(3)), m.group(4)) if m else None)
    day = 0
    days = []
    prev = None
    for item in raw:
        if item is not None:
            if prev is not None and item[0] < prev - 3600:
                day += 1
            prev = item[0]
        days.append(day)
    last = day
    base = time.localtime(mtime)
    midnight = time.mktime((base.tm_year, base.tm_mon, base.tm_mday,
                            0, 0, 0, 0, 0, -1))
    out = []
    for item, d in zip(raw, days):
        if item is None:
            out.append(None)
        else:
            out.append((midnight - (last - d) * 86400 + item[0], item[1]))
    return out


def parse_log(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().split("\n")
    except OSError:
        return []
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = time.time()

    name = os.path.basename(path)
    out = []
    cur = None
    hero = ""
    for num, item in enumerate(stamps(lines, mtime)):
        if item is None:
            continue
        when, msg = item

        m = PICKED.match(msg) or SWEEP.match(msg) or SWITCH.match(msg)
        if m:
            hero = m.group(1).replace("_", " ").title()

        if (msg.startswith("матч пошёл") or msg.startswith("матч уже идёт")
                or msg.startswith("====")):
            if cur and worth_keeping(cur):
                finish(cur)
                out.append(cur)
            cur = None
            if not msg.startswith("===="):
                cur = blank(when)
                cur["hero"] = hero
                cur["file"] = name
                cur["key"] = "%s:%d" % (name, num)
            continue
        if cur is None:
            continue
        if not cur["over"]:
            cur["end"] = when

        m = TOTAL.match(msg)
        if m:
            g = m.groups()
            cur.update(secs=int(g[0]) * 60 + int(g[1]), waves=int(g[2]),
                       bosses=int(g[3]), bkb=int(g[4]), bkb_skipped=int(g[5]),
                       deaths=int(g[6]), satanic=int(g[7]),
                       talents=int(g[8]), talents_plan=int(g[9]),
                       items=int(g[10]), items_plan=int(g[11]),
                       level=int(g[12]), why=g[13], done=True)
            out.append(cur)
            cur = None
            continue

        m = OLD_END.match(msg)
        if m:
            cur["waves"] = max(cur["waves"], int(m.group(1)))
            cur["bosses"] = max(cur["bosses"], int(m.group(2)))
            cur["secs"] = round(when - cur["start"])
            out.append(cur)
            cur = None
            continue

        m = ENDED.match(msg)
        if m:
            cur["why"] = m.group(1) or "конец матча"
            cur["over"] = True
            continue
        if WAVE.match(msg):
            cur["waves"] += 1
            if "<-- БОСС" in msg:
                cur["bosses"] += 1
            continue
        m = BKB_HIT.match(msg)
        if m:
            cur["bkb"] = max(cur["bkb"], int(m.group(1)))
            continue
        if BKB_SKIP.match(msg):
            cur["bkb_skipped"] += 1
            continue
        if msg.startswith("герой умер"):
            cur["deaths"] += 1
            continue
        if msg.startswith("САТАНИК"):
            cur["satanic"] += 1
            continue
        m = BOUGHT.match(msg)
        if m:
            got, plan = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            cur["items"] = max(cur["items"], int(got))
            cur["items_plan"] = int(plan)
            continue
        m = TALENTS_ALL.match(msg)
        if m:
            cur["talents"] = max(cur["talents"], int(m.group(1)))
            cur["talents_plan"] = int(m.group(2))
            continue
        m = STATE.match(msg)
        if m:
            cur["level"] = max(cur["level"], int(m.group(1)))
            cur["talents"] = max(cur["talents"], int(m.group(2)))
            cur["talents_plan"] = int(m.group(3))

    if cur and worth_keeping(cur):
        finish(cur)
        out.append(cur)
    return out


def log_files():
    files = glob.glob(os.path.join(core.LOGS_DIR, "bot.log*"))
    return sorted(files, key=os.path.getmtime, reverse=True)


HIDDEN_FILE = os.path.join(core.LOGS_DIR, "stats_hidden.json")


def hidden():
    try:
        with open(HIDDEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def set_hidden(keys):
    with open(HIDDEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(keys), f, ensure_ascii=False, indent=1)


def hide(key, on=True):
    keys = hidden()
    before = len(keys)
    keys.add(key) if on else keys.discard(key)
    if len(keys) != before:
        set_hidden(keys)
        return True
    return False


def all_runs(with_hidden=False):
    out = []
    for p in log_files():
        out += parse_log(p)
    skip = hidden()
    for r in out:
        r["hidden"] = r["key"] in skip
    if not with_hidden:
        out = [r for r in out if not r["hidden"]]
    out.sort(key=lambda r: r["end"], reverse=True)
    return out


def totals(runs):
    t = {f: 0 for f in FIELDS}
    t["matches"] = len(runs)
    for r in runs:
        for f in FIELDS:
            t[f] += r.get(f, 0)
    t["full"] = sum(1 for r in runs
                    if r["talents_plan"] and r["talents"] >= r["talents_plan"]
                    and r["items_plan"] and r["items"] >= r["items_plan"])
    tries = t["bkb"] + t["bkb_skipped"]
    t["bkb_rate"] = round(100.0 * t["bkb"] / tries) if tries else 0
    with_waves = [r for r in runs if r["waves"]]
    t["counted"] = len(with_waves)
    t["per_match"] = (round(1.0 * t["waves"] / len(with_waves), 1)
                      if with_waves else 0)
    return t


def hms(secs):
    secs = int(secs)
    if secs >= 3600:
        return "%d ч %02d мин" % (secs // 3600, secs % 3600 // 60)
    return "%d:%02d" % (secs // 60, secs % 60)


def main():
    files = sys.argv[1:] or log_files()
    runs = []
    for p in files:
        runs += parse_log(p if os.path.isabs(p) else os.path.join(HERE, p))
    runs.sort(key=lambda r: r["end"], reverse=True)
    t = totals(runs)
    print("матчей %d (план выполнен полностью в %d), волн %d "
          "(%.1f за матч по %d матчам), боссов %d"
          % (t["matches"], t["full"], t["waves"], t["per_match"],
             t["counted"], t["bosses"]))
    print("BKB %d из %d попыток (%d%%), смертей %d, сатаник %d, в игре %s"
          % (t["bkb"], t["bkb"] + t["bkb_skipped"], t["bkb_rate"],
             t["deaths"], t["satanic"], hms(t["secs"])))
    print()
    print("%-16s %-14s %7s %6s %6s %5s %4s %7s %6s %4s" % (
        "конец", "герой", "длит", "волны", "боссы", "BKB", "смр", "таланты",
        "предм", "ур"))
    for r in runs:
        print("%-16s %-14s %7s %6d %6d %5d %4d %4d/%-2d %3d/%-2d %4d" % (
            time.strftime("%d.%m %H:%M", time.localtime(r["end"])),
            (r.get("hero") or "?")[:14],
            hms(r["secs"]), r["waves"], r["bosses"], r["bkb"], r["deaths"],
            r["talents"], r["talents_plan"], r["items"], r["items_plan"],
            r["level"]))


if __name__ == "__main__":
    main()
