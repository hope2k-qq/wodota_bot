"""
GSI-пробник для Dota 2.

Слушает порт 3100, принимает всё, что шлёт игра, и отвечает на два вопроса:
  1) какие блоки/поля вообще приходят (особенно в кастомке);
  2) есть ли среди них то, что нужно для макроса.

Запуск:  py gsi_probe.py
Стоп:    Ctrl+C  -> печатает отчёт и пишет gsi_report.txt
Сырые данные пишутся построчно в gsi_log.jsonl
"""

import json
import re
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 3100
LOG_FILE = "gsi_log.jsonl"
REPORT_FILE = "gsi_report.txt"

CHECKLIST = [
    ("map.game_state",      "триггер: матч реально начался"),
    ("map.clock_time",      "триггер: игровое время -> отсечка 10:00"),
    ("map.customgamename",  "подтверждение, что это кастомка"),
    ("hero.level",          "сколько уровней доступно для прокачки"),
    ("hero.alive",          "жив ли герой"),
    ("hero.health_percent", "здоровье"),
    ("hero.xpos",           "координаты героя (нужны, только если захотим кликать по миру)"),
    ("hero.ypos",           "координаты героя"),
    ("player.gold",         "хватает ли золота на покупку"),
    ("abilities.ability0.level", "уровень абилки -> проверка, что прокачка прошла"),
    ("items.slot0.name",    "проверка, что предмет куплен"),
]

NUMERIC_KEY = re.compile(r"^(o|object|item|player|team)?\d+$")
DIFF_BLOCKS = ("previously", "added")

schema = {}
top_blocks = {}
lock = threading.Lock()
stats = {"payloads": 0, "first": None, "last": None, "bytes": 0}
latest = {}


def note(path, value):
    e = schema.get(path)
    if e is None:
        e = schema[path] = {"n": 0, "nonempty": 0, "samples": []}
    e["n"] += 1
    empty = value is None or value == "" or value == [] or value == {}
    if not empty:
        e["nonempty"] += 1
        s = repr(value)
        if len(s) > 60:
            s = s[:57] + "..."
        if s not in e["samples"] and len(e["samples"]) < 3:
            e["samples"].append(s)


def walk(node, prefix=""):
    if isinstance(node, dict):
        if not node:
            note(prefix, {})
            return
        for k, v in node.items():
            key = "*" if NUMERIC_KEY.match(k) else k
            walk(v, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, list):
        note(prefix + "[]", node)
    else:
        note(prefix, node)


def dig(data, path):
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def ingest(data):
    with lock:
        stats["payloads"] += 1
        now = time.time()
        if stats["first"] is None:
            stats["first"] = now
        stats["last"] = now
        for block, body in data.items():
            top_blocks[block] = top_blocks.get(block, 0) + 1
            if block in DIFF_BLOCKS:
                if isinstance(body, dict):
                    for sub in body:
                        note(f"{block}.{sub}", "<diff>")
                continue
            walk(body, block)
        latest.clear()
        latest.update(data)


def status_line(d):
    gs = dig(d, "map.game_state") or "-"
    gs = str(gs).replace("DOTA_GAMERULES_STATE_", "")
    clock = dig(d, "map.clock_time")
    clock = f"{int(clock)//60:02d}:{int(clock)%60:02d}" if isinstance(clock, (int, float)) else "--:--"
    cg = dig(d, "map.customgamename") or ""
    hero = dig(d, "hero.name") or "-"
    lvl = dig(d, "hero.level")
    hp = dig(d, "hero.health_percent")
    gold = dig(d, "player.gold")
    x, y = dig(d, "hero.xpos"), dig(d, "hero.ypos")
    pos = f"{int(x)},{int(y)}" if isinstance(x, (int, float)) and isinstance(y, (int, float)) else "n/a"
    blocks = ",".join(k for k in d if k not in DIFF_BLOCKS)
    return (f"[{stats['payloads']:5d}] {gs:<14} t={clock} cg={cg or '-':<18} "
            f"{hero:<22} lvl={lvl} hp={hp} gold={gold} pos={pos}\n"
            f"         blocks: {blocks}")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    last_print = 0.0

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()
        stats["bytes"] += n
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as ex:
            print(f"!! не разобрал JSON: {ex}")
            return
        ingest(data)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
        now = time.time()
        if now - Handler.last_print > 1.0:
            Handler.last_print = now
            print(status_line(data), flush=True)


def report():
    out = []
    w = out.append
    dur = (stats["last"] - stats["first"]) if stats["first"] else 0
    rate = stats["payloads"] / dur if dur > 0 else 0
    w("=" * 78)
    w(f"GSI ОТЧЁТ   пакетов: {stats['payloads']}   длительность: {dur:.0f} c   "
      f"частота: {rate:.1f}/с   объём: {stats['bytes']/1024:.0f} КБ")
    w("=" * 78)
    w("")
    w("--- ЧЕКЛИСТ: нужно для макроса ---")
    for path, why in CHECKLIST:
        e = schema.get(path)
        if e and e["nonempty"] > 0:
            mark, detail = "ЕСТЬ   ", f"пример: {e['samples'][0] if e['samples'] else ''}"
        elif e:
            mark, detail = "ПУСТО  ", "поле приходит, но всегда пустое/null"
        else:
            mark, detail = "НЕТ    ", "не приходило ни разу"
        w(f"  [{mark}] {path:<28} {detail}")
        w(f"              ^ {why}")
    w("")
    w("--- ТОП-УРОВНЕВЫЕ БЛОКИ ---")
    for k, v in sorted(top_blocks.items(), key=lambda kv: -kv[1]):
        w(f"  {k:<18} x{v}")
    w("")
    w("--- ПОЛНАЯ СХЕМА (всё, что пришло) ---")
    w(f"  {'путь':<52} {'всего':>6} {'непусто':>8}  примеры")
    for path in sorted(schema):
        e = schema[path]
        w(f"  {path:<52} {e['n']:>6} {e['nonempty']:>8}  {' | '.join(e['samples'])}")
    text = "\n".join(out)
    print("\n" + text)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"\nОтчёт сохранён: {REPORT_FILE}")
    print(f"Сырые данные:   {LOG_FILE}")


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Слушаю 127.0.0.1:{PORT} — жду данные от Dota 2.")
    print("Если ничего не идёт: нет флага -gamestateintegration или игра не перезапущена.")
    print("Ctrl+C — остановить и показать отчёт.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлено.")
    finally:
        srv.server_close()
        if stats["payloads"] == 0:
            print("\nНЕ ПРИШЛО НИ ОДНОГО ПАКЕТА.")
            print("Проверь: 1) флаг -gamestateintegration в параметрах запуска;")
            print("         2) Dota перезапущена ПОСЛЕ создания конфига;")
            print("         3) порт в конфиге = порт пробника.")
        else:
            report()


if __name__ == "__main__":
    main()
