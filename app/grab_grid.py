"""Достать дерево талантов любого героя из talents_list.lua."""
import io
import json
import os
import re
import sys

SRC = r"C:\hope2k\custom_3\scripts\vscripts\configs\talents_list.lua"
HERO = sys.argv[1] if len(sys.argv) > 1 else "npc_dota_hero_morphling"
OUT = r"C:\hope2k\react\wodota-autoplay\talents_grid.json"

raw = io.open(SRC, encoding="utf-8", errors="replace").read()
hexs = "".join(re.findall(r"^[0-9A-F]+$", raw, re.M))
txt = bytes.fromhex(hexs).decode("utf-8", "replace")

i = txt.index('["%s"]' % HERO)
m = re.search(r'\n\t\["npc_dota_hero_', txt[i + 10:])
block = txt[i:i + 10 + m.start()] if m else txt[i:]

grid = {}
depth = 0
tree = row = None
col = 0
bad = []
for line in block.split("\n"):
    s = line.strip()
    if s:
        mt = re.match(r'^\[(\d+)\] = \{$', s)
        if mt:
            n = int(mt.group(1))
            if depth == 1:
                tree, row = n, None
            elif depth == 2:
                row, col = n, 0
        elif depth == 3 and s.startswith("{"):
            col += 1
            if not re.match(r'^\{"empty_\d+"\},?$', s):
                mf = re.match(r'^\{"([^"]+)",\s*"([^"]*)",\s*(\d+),\s*"([^"]*)",'
                              r'\s*\{(.*?)\}\},?\s*$', s)
                if mf:
                    rq = None
                    mr = re.match(r'^"([^"]+)",(\d+)$', mf.group(5).strip())
                    if mr:
                        rq = [mr.group(1), int(mr.group(2))]
                    grid["%d,%d,%d" % (tree, row, col)] = {
                        "mod": mf.group(1), "max": int(mf.group(3)),
                        "icon": mf.group(4), "req": rq}
                else:
                    bad.append(s[:80])
    depth += s.count("{") - s.count("}")

print("герой:", HERO)
print("ячеек:", len(grid), "| не разобрано:", bad or "нет")
TR = {1: "сила", 2: "ловкость", 3: "интеллект"}
for t in (1, 2, 3):
    ks = [k for k in grid if k.startswith("%d," % t)]
    print("  дерево %d %-10s ячеек %2d, рангов %2d"
          % (t, TR[t], len(ks), sum(grid[k]["max"] for k in ks)))

old = {}
if os.path.exists(OUT):
    old = json.load(io.open(OUT, encoding="utf-8"))
if "cells" in old and "hero" in old:
    old = {"_": old.get("_", ""),
           "row_need": old.get("row_need"),
           "_row_need": old.get("_row_need", ""),
           "_cells": old.get("_cells", ""),
           "heroes": {old["hero"]: old["cells"]}}
old.setdefault("row_need", [0, 4, 8, 12, 16, 20, 24])
old.setdefault("heroes", {})
old["heroes"][HERO] = {k: {"mod": v["mod"], "max": v["max"],
                           "icon": v["icon"], "req": v["req"]}
                       for k, v in sorted(
                           grid.items(),
                           key=lambda kv: [int(x) for x in kv[0].split(",")])}
old["_"] = ("Правила талантов по героям, вынуты из самой кастомки: "
            "talents_list.lua (там _G.herotalents зашит hex-ом) и "
            "custom_talents.js (line_level_info). Нужны для сверки плана "
            "через py bot.py --check. Добавить героя: "
            "py grab_grid.py npc_dota_hero_<имя>.")
io.open(OUT, "w", encoding="utf-8").write(
    json.dumps(old, ensure_ascii=False, indent=1))
print("в файле правил героев:", list(old["heroes"]))
