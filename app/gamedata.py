"""
Справочник игры: герои, предметы и цены — для выпадающих списков на
пульте и для закупа.

    py gamedata.py            пересобрать gamedata.json

Читает только обычные файлы: `scripts/npc` кастомки и две выгрузки в
`gamefiles/`, которые один раз достаёт `extract_gamefiles.py` из пака
Dota. В сам пак ни бот, ни этот сборщик не лазят.

Цена считается так же, как её считает игра: цена рецепта плюс всё, из
чего он собирается, и так вглубь. Сверка по одиннадцати предметам из
боевого билда — десять сошлись в точности. Одиннадцатый, шлем: расчёт
даёт цену покупки с нуля, а в игре часть компонентов уже куплена
строками выше, и платить надо меньше. Поэтому расчёт — отправная
точка, а настоящую цену бот замеряет при покупке и пишет в
prices.json; она главнее.
"""

import io
import json
import os
import re
import sys

import core

OUT = core.file("gamedata.json")
FILES = core.GAMEFILES_DIR

HERO_FIX = {
    "nevermore": "Shadow Fiend", "windrunner": "Windranger",
    "zuus": "Zeus", "skeleton_king": "Wraith King",
    "obsidian_destroyer": "Outworld Destroyer", "rattletrap": "Clockwerk",
    "shredder": "Timbersaw", "furion": "Nature's Prophet",
    "doom_bringer": "Doom", "wisp": "Io", "magnataur": "Magnus",
    "treant": "Treant Protector", "abyssal_underlord": "Underlord",
    "necrolyte": "Necrophos", "queenofpain": "Queen of Pain",
    "life_stealer": "Lifestealer", "centaur": "Centaur Warrunner",
}


def read(path):
    try:
        return io.open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def parse_items(text, out=None):
    """Блоки предметов из KV.

    Один предмет встречается в файле не раз — где-то рецепт, где-то
    описание, — поэтому поля накапливаются, а не затираются.
    """
    out = {} if out is None else out
    depth = 0
    cur = None
    inreq = False
    for line in text.split("\n"):
        s = line.strip()
        if s and not s.startswith("//"):
            m = re.match(r'^"([^"]+)"$', s)
            if m:
                if depth == 1:
                    cur = m.group(1)
                    out[cur] = out.get(cur) or {}
                    inreq = False
                elif m.group(1) == "ItemRequirements":
                    inreq = True
            kv = re.match(r'^"([^"]+)"\s+"([^"]*)"', s)
            if kv and cur:
                if inreq:
                    out[cur]["_req"] = kv.group(2)
                else:
                    out[cur][kv.group(1)] = kv.group(2)
        depth += line.count("{") - line.count("}")
        if depth <= 1:
            inreq = False
    return out


def parse_blocks(text):
    """Плоский разбор KV — для списка героев."""
    out = {}
    depth = 0
    cur = None
    for line in text.split("\n"):
        s = line.strip()
        if s and not s.startswith("//"):
            m = re.match(r'^"([^"]+)"$', s)
            if m and depth == 1:
                cur = m.group(1)
                out.setdefault(cur, {})
        depth += line.count("{") - line.count("}")
    return out


def titles(*texts):
    """Названия предметов: сперва кастомка, потом Dota."""
    out = {}
    pat = re.compile(r'"DOTA_Tooltip_[Aa]bility_(item_[a-z_0-9]+)"\s+"([^"]+)"')
    for text in texts:
        for m in pat.finditer(text):
            name, title = m.group(1), m.group(2)
            if "$" in title or title.startswith("+"):
                continue
            out.setdefault(name, title)
    return out


def pretty(ident, prefix):
    tail = ident[len(prefix):]
    return HERO_FIX.get(tail) or tail.replace("_", " ").title()


def build_costs(items):
    """Полная цена каждого предмета: рецепт плюс всё, из чего он собран.

    Рецепт — отдельный предмет item_recipe_*, у него поле ItemResult
    указывает, что получается, а ItemRequirements — из чего.
    """
    recipe_of = {}
    for name, v in items.items():
        if v.get("ItemResult"):
            recipe_of[v["ItemResult"]] = name

    def num(v):
        try:
            return int(v or 0)
        except ValueError:
            return 0

    def cost(name, seen=frozenset()):
        if name in seen or name not in items:
            return 0
        seen = seen | {name}
        r = items.get(recipe_of.get(name, ""), {})
        parts = [p for p in (r.get("_req") or "").split(";") if p]
        if not parts:
            return num(items[name].get("ItemCost"))
        return num(r.get("ItemCost")) + sum(cost(p, seen) for p in parts)

    return {n: cost(n) for n in items}


def main():
    cfg = core.load_config()
    addon = (sys.argv[1] if len(sys.argv) > 1
             else cfg.get("addon_dir", r"C:\hope2k\custom_3"))
    npc = os.path.join(addon, "scripts", "npc")

    dota_items = read(os.path.join(FILES, "items_dota.txt"))
    if not dota_items:
        print("нет gamefiles/items_dota.txt — сперва py extract_gamefiles.py")
        return
    items = parse_items(dota_items)
    parse_items(read(os.path.join(npc, "npc_items_custom.txt")), items)
    costs = build_costs(items)

    names = titles(read(os.path.join(addon, "resource", "addon_russian.txt")),
                   read(os.path.join(FILES, "items_names.txt")))

    out_items = []
    for name in sorted(items):
        if not name.startswith("item_") or name.startswith("item_recipe_"):
            continue
        if not costs.get(name):
            continue
        out_items.append({"name": name,
                          "title": names.get(name) or pretty(name, "item_"),
                          "cost": costs[name]})
    out_items.sort(key=lambda i: i["title"].lower())

    heroes = [{"name": n, "title": pretty(n, "npc_dota_hero_")}
              for n in sorted(parse_blocks(
                  read(os.path.join(npc, "npc_heroes_custom.txt"))))
              if n.startswith("npc_dota_hero_")]

    data = {
        "_": "Справочник для пульта, пересобрать: py gamedata.py. Цена "
             "посчитана как в игре — рецепт плюс всё, из чего он собран. "
             "Это цена покупки с нуля; когда часть компонентов уже куплена "
             "строками выше, платить надо меньше, и тогда главнее то, что "
             "бот замерил сам (prices.json).",
        "addon_dir": addon,
        "heroes": heroes,
        "items": out_items,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("героев: %d, предметов с ценой: %d -> %s"
          % (len(heroes), len(out_items), os.path.basename(OUT)))


if __name__ == "__main__":
    main()
