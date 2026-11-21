"""
Переделать таланты и закуп всех билдов под один образец.

    py restyle_builds.py            показать, что получится, не трогая файлы
    py restyle_builds.py --write    записать
    py restyle_builds.py --write --all   вместе с образцовыми билдами

Образец — ручные билды Drow Ranger и Furion: около тридцати рангов в силу
(здоровье, урон, сопротивление магии, броня — чтобы пережить волны), потом
всё остальное в ловкость, всего 57 кликов. Старые базовые билды брали в
силу только девять рангов и были заметно мягче.

План не переносится клетками: у каждого героя своё дерево, и чужие
координаты попали бы в пустоту. Берём только порядок ценностей, а дальше
жадно собираем по правилам аддона — на каждом шаге самое ценное из того,
что сейчас разрешено взять. Так план всегда проходит проверку.

Сохраняется всё остальное в билде: герой, точка пика, выключатели, свои
реакции. Трогаются только talents.plan и items.
"""

import collections
import io
import json
import os
import sys

import core
import make_builds as mb

STR_TREE, AGI_TREE = 1, 2
STR_GOAL = 30
TOTAL = 57

VALUE_STR = {
    "hp1": 100,
    "attack1": 98,
    "str3": 95,
    "str6": 92,
    "cloak": 90,
    "sasha": 85,
    "quickhp": 80,
    "str4": 75,
    "armor2": 70,
    "str5": 65,
    "sliver": 60,
    "hp2": 55,
    "octar": 50,
    "regenhp2": 45,
    "regenhp1": 40,
    "armor1": 35,
    "spell": 25,
    "blood": 25,
}

ITEMS_RANGED = "item_veil_desolator_custom"
ITEMS_MELEE = "item_bfury"
ITEMS = [
    {"name": "item_hand_of_midas_custom", "stash": True,
     "_": "первым делом, ради дохода. Сразу после покупки уходит правой "
          "кнопкой в тайник, слот не занимает."},
    {"name": "__first__"},
    {"name": "item_lesser_crit"},
    {"name": "item_greater_crit"},
    {"name": "item_satanic"},
    {"name": "item_butterfly"},
    {"name": "item_vladmir"},
    {"name": "item_butterfly"},
    {"name": "item_helm_of_the_overlord_custom",
     "_": "доплата: собирается из Helm of the Dominator и владимира, а "
          "владимир куплен строкой выше."},
    {"name": "item_black_king_bar", "role": "bkb"},
    {"name": "item_moon_sange", "role": "use_once"},
    {"name": "item_moon_aghanim", "role": "use_once"},
    {"name": "item_mage_destroyer", "role": "boss_swap", "slot": 1,
     "_": "лежит в рюкзаке, на волне босса переезжает в слот 1"},
    {"name": "item_book_agi", "role": "books"},
]

PATTERN = ("drow_ranger", "furion")


def value_of(cell, tree):
    if tree == STR_TREE:
        return VALUE_STR.get(cell["icon"], mb.OTHER)
    return mb.VALUE.get(cell["icon"], mb.OTHER)


def grow(cells, need, tree, limit, lvl, taken):
    bymod = {v["mod"]: k for k, v in cells.items()}
    mine = {k: v for k, v in cells.items() if k.startswith("%d," % tree)}
    plan = []
    while taken[tree] < limit:
        ready = []
        for k, v in mine.items():
            if lvl.get(k, 0) >= v["max"]:
                continue
            row = int(k.split(",")[1])
            if taken[tree] < need[row - 1]:
                continue
            req = v["req"]
            if req and lvl.get(bymod.get(req[0]), 0) != req[1]:
                continue
            ready.append(k)
        if not ready:
            break
        k = max(ready, key=lambda x: (value_of(mine[x], tree),
                                      -int(x.split(",")[1]),
                                      -int(x.split(",")[2])))
        lvl[k] = lvl.get(k, 0) + 1
        taken[tree] += 1
        plan.append([int(n) for n in k.split(",")])
    return plan


def make_plan(cells, need):
    lvl, taken = {}, collections.Counter()
    plan = grow(cells, need, STR_TREE, STR_GOAL, lvl, taken)
    plan += grow(cells, need, AGI_TREE, TOTAL - taken[STR_TREE], lvl, taken)
    if len(plan) < TOTAL:
        plan += grow(cells, need, STR_TREE,
                     taken[STR_TREE] + TOTAL - len(plan), lvl, taken)
    return plan


def attack_kind():
    path = os.path.join(core.GAMEFILES_DIR, "heroes_attack.json")
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except Exception:
        return {}


def make_items(gsi, kinds):
    melee = (kinds.get(gsi) or {}).get("attack") == "melee"
    out = []
    for e in ITEMS:
        e = dict(e)
        if e["name"] == "__first__":
            e["name"] = ITEMS_MELEE if melee else ITEMS_RANGED
        out.append(e)
    return out


def main():
    write = "--write" in sys.argv
    every = "--all" in sys.argv
    grid = core.read_json(core.file("talents_grid.json")) or {}
    heroes, need = grid.get("heroes") or {}, grid.get("row_need") or []
    kinds = attack_kind()
    if not heroes or not need:
        print("нет talents_grid.json — без правил аддона план не собрать")
        return 1

    done = skipped = bad = 0
    for name in core.hero_files():
        path = core.hero_path(name)
        data = core.read_json(path)
        if not data:
            print("%-22s файл не читается" % name)
            bad += 1
            continue
        if name in PATTERN and not every:
            print("%-22s образец, не трогаю" % name)
            skipped += 1
            continue
        gsi = data.get("gsi_name") or ""
        cells = heroes.get(gsi)
        if not cells:
            print("%-22s нет правил талантов (%s)" % (name, gsi or "герой не задан"))
            skipped += 1
            continue
        plan = make_plan(cells, need)
        why = mb.check(plan, cells, need)
        if why:
            print("%-22s план не прошёл проверку: %s" % (name, why))
            bad += 1
            continue
        was = len((data.get("talents") or {}).get("plan") or [])
        items = make_items(gsi, kinds)
        if write:
            data.setdefault("talents", {})["plan"] = plan
            data["items"] = items
            with io.open(path, "w", encoding="utf-8", newline="\r\n") as f:
                f.write(json.dumps(data, ensure_ascii=False, indent=2))
        cnt = collections.Counter(p[0] for p in plan)
        print("%-22s таланты %d -> %d (сила %d, ловкость %d), закуп %d"
              % (name, was, len(plan), cnt[STR_TREE], cnt[AGI_TREE],
                 len(items)))
        done += 1

    print()
    print("переделано %d, пропущено %d, с ошибкой %d%s"
          % (done, skipped, bad, "" if write else " — это был показ без записи"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
