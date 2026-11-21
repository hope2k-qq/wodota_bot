"""
Базовые билды на всех героев кастомки.

    py make_builds.py            создать недостающие
    py make_builds.py --force    перезаписать и уже существующие

Таланты — всё в ловкость. Порядок не произвольный: сначала то, что
просили — проценты к ловкости, прирост всего наносимого урона и
уклонение, — потом остальное. Внутри дерева порядок всё равно диктуют
правила кастомки: ряд открывается, только когда в дереве набрано
0/4/8/12/16/20/24 ранга, а у части талантов есть требование «такой-то
ровно такого ранга». Поэтому план не пишется руками, а собирается
жадно: на каждом шаге берём самый ценный из тех, что сейчас доступны.

Предметы зависят от дальности атаки: ближнему Battle Fury, дальнему
Magic Arrows, остальное одинаково.

Точка пика не заполняется — её снимают калибровкой:
    py calibrate.py hero_pick <билд>
"""

import collections
import io
import json
import os
import re
import sys

import core

TALENTS_LUA = r"scripts\vscripts\configs\talents_list.lua"
GRID_FILE = core.file("talents_grid.json")
HEROES_DIR = core.HEROES_DIR
ATTACK_FILE = os.path.join(core.GAMEFILES_DIR, "heroes_attack.json")
AGI = 2

VALUE = {
    "agi5": 100,
    "attack2": 95,
    "miss2": 90,
    "miss1": 85,
    "yasha": 80,
    "agi6": 75,
    "agi4": 70,
    "agi3": 65,
    "attackspeed2": 50,
    "attackspeed1": 45,
    "mask1": 40,
    "armor1": 30,
    "grovebow": 25,
    "speed2": 20,
    "speed1": 15,
}
OTHER = 10

OPENING = [("str3", 2), ("hp1", 3), ("str6", 1), ("cloak", 2), ("sasha", 1)]
STR = 1

MELEE = ["item_bfury"]
RANGED = ["item_veil_desolator_custom"]
TAIL = ["item_butterfly", "item_butterfly",
        "item_helm_of_the_overlord_custom",
        "item_greater_crit", "item_greater_crit"]
BOOK = ("item_book_agi", "books")


def decode_talents(addon):
    raw = io.open(os.path.join(addon, TALENTS_LUA),
                  encoding="utf-8", errors="replace").read()
    hexs = "".join(re.findall(r"^[0-9A-F]+$", raw, re.M))
    return bytes.fromhex(hexs).decode("utf-8", "replace")


def hero_grid(txt, hero):
    start = txt.find("_G.herotalents")
    if start < 0:
        start = 0
    i = txt.find('["%s"]' % hero, start)
    if i < 0:
        return {}
    m = re.search(r'\n\t\["npc_dota_hero_', txt[i + 10:])
    block = txt[i:i + 10 + m.start()] if m else txt[i:]
    grid = {}
    depth = 0
    tree = row = None
    col = 0
    for line in block.split("\n"):
        s = line.strip()
        if s:
            mt = re.match(r'^\[(\d+)\] = \{$', s)
            if mt:
                num = int(mt.group(1))
                if depth == 1:
                    tree, row = num, None
                elif depth == 2:
                    row, col = num, 0
            elif depth == 3 and s.startswith("{"):
                col += 1
                if not re.match(r'^\{"empty_\d+"\},?$', s):
                    mf = re.match(r'^\{"([^"]+)",\s*"([^"]*)",\s*(\d+),'
                                  r'\s*"([^"]*)",\s*\{(.*?)\}\},?\s*$', s)
                    if mf:
                        rq = None
                        mr = re.match(r'^"([^"]+)",(\d+)$', mf.group(5).strip())
                        if mr:
                            rq = [mr.group(1), int(mr.group(2))]
                        grid["%d,%d,%d" % (tree, row, col)] = {
                            "mod": mf.group(1), "max": int(mf.group(3)),
                            "icon": mf.group(4), "req": rq}
        depth += s.count("{") - s.count("}")
    return grid


def value(cell):
    return VALUE.get(cell["icon"], OTHER)


def plan_opening(cells):
    where = {}
    for k, v in cells.items():
        if k.startswith("%d," % STR):
            where.setdefault(v["icon"], k)
    plan = []
    for icon, ranks in OPENING:
        k = where.get(icon)
        if not k:
            return None, "в дереве силы нет %s" % icon
        plan += [[int(x) for x in k.split(",")]] * ranks
    return plan, None


def plan_agility(cells, need):
    bymod = {v["mod"]: k for k, v in cells.items()}
    mine = {k: v for k, v in cells.items() if k.startswith("%d," % AGI)}
    lvl = {k: 0 for k in mine}
    taken = 0
    plan = []
    while True:
        ready = []
        for k, v in mine.items():
            if lvl[k] >= v["max"]:
                continue
            row = int(k.split(",")[1])
            if taken < need[row - 1]:
                continue
            req = v["req"]
            if req and lvl.get(bymod.get(req[0]), None) != req[1]:
                continue
            ready.append(k)
        if not ready:
            return plan
        k = max(ready, key=lambda x: (value(mine[x]),
                                      -int(x.split(",")[1]),
                                      -int(x.split(",")[2])))
        lvl[k] += 1
        taken += 1
        plan.append([int(n) for n in k.split(",")])


def check(plan, cells, need):
    bymod = {v["mod"]: k for k, v in cells.items()}
    lvl = {k: 0 for k in cells}
    cnt = collections.Counter()
    for i, (t, r, c) in enumerate(plan, 1):
        k = "%d,%d,%d" % (t, r, c)
        v = cells.get(k)
        if not v:
            return "шаг %d: пустая ячейка" % i
        if lvl[k] >= v["max"]:
            return "шаг %d: у %s рангов всего %d" % (i, v["icon"], v["max"])
        if cnt[t] < need[r - 1]:
            return ("шаг %d: ряд %d требует %d, есть %d"
                    % (i, r, need[r - 1], cnt[t]))
        if v["req"] and lvl.get(bymod.get(v["req"][0]), None) != v["req"][1]:
            return "шаг %d: не выполнено требование %s" % (i, v["req"][0])
        lvl[k] += 1
        cnt[t] += 1
    return None


def build(hero, title, melee, plan):
    head = [m for m in (MELEE if melee else RANGED)]
    return collections.OrderedDict([
        ("_", "Базовый билд: все таланты в ловкость, предметы под "
              "дальность атаки. Сделан py make_builds.py, правится как "
              "любой другой."),
        ("title", title),
        ("gsi_name", hero),
        ("_gsi_name", "Имя героя в GSI. По нему бот проверяет, что пик "
                      "не промахнулся."),
        ("pick_at", {}),
        ("_pick_at", "Куда кликать на экране выбора героя, по профилям "
                     "экрана. Пусто — не снято: py calibrate.py hero_pick "
                     "<билд>."),
        ("functions", collections.OrderedDict(
            [(k, True) for k in ("talents", "abilities", "items", "late",
                                 "books", "bkb", "satanic", "disarm",
                                 "death", "moon", "boss_swap")])),
        ("_functions", "Что боту делать за этого героя. Роли, под которые "
                       "нет предметов, просто простаивают."),
        ("talents", {"plan": plan}),
        ("_talents", "[дерево, строка, колонка] — один клик на запись, по "
                     "порядку. Сначала девять в силу (1): здоровье, "
                     "сопротивление магии, снижение получаемого урона — "
                     "чтобы пережить ранние волны. Дальше всё в ловкость "
                     "(2) по ценности: проценты к ловкости, весь "
                     "наносимый урон, уклонение — раньше прочего."),
        ("abilities", collections.OrderedDict([
            ("max_level", collections.OrderedDict(
                [("ult", 3), ("slot1", 4), ("slot2", 4), ("slot3", 4)])),
            ("_max_level", "Клавиши на четвёртую способность в общем "
                           "конфиге нет, поэтому качаются три и ульта. "
                           "Если у героя их больше — допиши keys и "
                           "priority сюда."),
            ("autocast", {"slot": "", "combo": []}),
        ])),
        ("items", [collections.OrderedDict([("name", n)])
                   for n in head + TAIL]
                  + [collections.OrderedDict([("name", BOOK[0]),
                                              ("role", BOOK[1])])]),
        ("_items", "Закуп по порядку: что выше, покупается раньше. Цена и "
                   "название подтягиваются сами."),
        ("reactions", collections.OrderedDict([
            ("disarm", {"after_s": 6, "key": ["e"]}),
            ("death", {"key": ["f1"]}),
        ])),
    ])


def main():
    force = "--force" in sys.argv
    cfg = core.load_config()
    addon = cfg.get("addon_dir", r"C:\hope2k\custom_3")
    txt = decode_talents(addon)

    heroes = sorted(set(re.findall(
        r'"(npc_dota_hero_[a-z_0-9]+)"',
        io.open(os.path.join(addon, "scripts", "npc",
                             "npc_heroes_custom.txt"),
                encoding="utf-8", errors="replace").read())))
    attack = core.read_json(ATTACK_FILE, {})
    grid_all = core.read_json(GRID_FILE, {})
    grid_all.setdefault("row_need", [0, 4, 8, 12, 16, 20, 24])
    grid_all.setdefault("heroes", {})
    need = grid_all["row_need"]

    made = skipped = 0
    problems = []
    for hero in heroes:
        cells = hero_grid(txt, hero)
        if not cells:
            problems.append("%s: дерева нет в talents_list.lua" % hero)
            continue
        grid_all["heroes"][hero] = cells

        short = hero[len("npc_dota_hero_"):]
        path = os.path.join(HEROES_DIR, "%s.json" % short)
        if os.path.exists(path):
            old = core.read_json(path)
            mine = str(old.get("_", "")).startswith("Базовый билд")
            if not (force and mine):
                skipped += 1
                continue

        opening, why = plan_opening(cells)
        if why:
            problems.append("%s: %s" % (short, why))
            continue
        plan = opening + plan_agility(cells, need)
        bad = check(plan, cells, need)
        if bad:
            problems.append("%s: %s" % (short, bad))
            continue
        info = attack.get(hero) or {}
        if isinstance(info, str):
            info = {"attack": info}
        melee = info.get("attack") != "ranged"
        data = build(hero, short.replace("_", " ").title(), melee, plan)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        made += 1

    grid_all["_"] = ("Правила талантов по героям, вынуты из самой кастомки: "
                     "talents_list.lua (там _G.herotalents зашит hex-ом) и "
                     "custom_talents.js (line_level_info). Нужны для сверки "
                     "плана через py bot.py --check.")
    with open(GRID_FILE, "w", encoding="utf-8") as f:
        json.dump(grid_all, f, ensure_ascii=False, indent=1)

    print("героев в кастомке: %d" % len(heroes))
    print("правил талантов в файле: %d" % len(grid_all["heroes"]))
    print("билдов создано: %d, пропущено (уже есть): %d" % (made, skipped))
    if problems:
        print("не получилось:")
        for p in problems:
            print("  ", p)


if __name__ == "__main__":
    main()
