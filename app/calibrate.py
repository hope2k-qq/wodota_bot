"""
Снятие координат UI, которые нельзя вычислить из конфигов аддона.

Запусти скрипт, зайди в кастомку обычным способом и наводи курсор на то,
что он просит. Точки идут в том же порядке, в каком появляются экраны,
так что за один заход в игру снимается всё.

    F8  — записать точку под курсором
    F9  — пропустить
    ESC — выйти и сохранить

Первые шесть точек — экраны до матча (сервер, сложность, герой).
Последние четыре — уже в бою: панель талантов и магазин.

Сетка талантов тут НЕ нужна: она уже посчитана в config.json.
"""

import json
import os
import sys
import time

import core

VK_F8, VK_F9, VK_ESC = 0x77, 0x78, 0x1B

TARGETS = [
    ("server_pick",         "[1/3 сервер]   нужный СЕРВЕР"),
    ("server_confirm",      "[1/3 сервер]   кнопка подтверждения сервера"),
    ("difficulty_pick",     "[2/3 сложность] нужная СЛОЖНОСТЬ"),
    ("difficulty_confirm",  "[2/3 сложность] подтверждение (нет кнопки — F9)"),
    ("hero_pick",           "[3/3 герой]    портрет героя (пишется в билд)"),
    ("hero_pick_confirm",   "[3/3 герой]    подтверждение (нет кнопки — F9)"),
    ("talent_panel_toggle", "[в матче] кнопка, открывающая панель талантов"),
    ("shop_toggle",         "[в матче] кнопка, открывающая магазин"),
    ("shop_search_field",   "[в матче] поле поиска внутри магазина"),
    ("shop_first_result",   "[в матче] место ПЕРВОГО результата поиска"),
    ("quickbuy_slot",       "[в матче] слот КВИКБАЯ внизу справа (куда ложится книга)"),
    ("inv_slot1",           "[в матче] слот инвентаря 1"),
    ("inv_slot2",           "[в матче] слот инвентаря 2"),
    ("inv_slot3",           "[в матче] слот инвентаря 3"),
    ("inv_slot4",           "[в матче] слот инвентаря 4"),
    ("inv_slot5",           "[в матче] слот инвентаря 5"),
    ("inv_slot6",           "[в матче] слот инвентаря 6"),
    ("inv_slot7",           "[в матче] слот инвентаря 7 (рюкзак)"),
    ("inv_slot8",           "[в матче] слот инвентаря 8 (рюкзак)"),
    ("inv_slot9",           "[в матче] слот инвентаря 9 (рюкзак)"),
]


def cursor():
    class P(core.ctypes.Structure):
        _fields_ = [("x", core.ctypes.c_long), ("y", core.ctypes.c_long)]
    p = P()
    core.user32.GetCursorPos(core.ctypes.byref(p))
    return [p.x, p.y]


def down(vk):
    return bool(core.user32.GetAsyncKeyState(vk) & 0x8000)


def wait_release(vk):
    while down(vk):
        time.sleep(0.03)


ATTR_ORDER = [("strength", "СИЛА"), ("agility", "ЛОВКОСТЬ"),
              ("intellect", "ИНТЕЛЛЕКТ"), ("all", "УНИВЕРСАЛЫ")]


def hero_attrs():
    data = core.read_json(os.path.join(core.GAMEFILES_DIR,
                                       "heroes_attack.json"), {})
    out = {}
    for name, v in data.items():
        out[name] = (v.get("attr") if isinstance(v, dict) else "all") or "all"
    return out


def walk_picks(screen, only_missing=True):
    attrs = hero_attrs()
    rank = {a: i for i, (a, _) in enumerate(ATTR_ORDER)}
    names = core.hero_files()
    rows = []
    for name in names:
        hero = core.read_json(core.hero_path(name))
        gsi = hero.get("gsi_name") or ""
        at = hero.get("pick_at")
        at = at.get(screen) if isinstance(at, dict) else at
        have = bool(at and any(at))
        if have and only_missing:
            continue
        attr = attrs.get(gsi, "all")
        rows.append((rank.get(attr, 9), gsi, name,
                     hero.get("title") or name, have, attr))
    rows.sort()
    todo = [(r[2], r[3], r[4], r[5]) for r in rows]
    if not todo:
        print("У всех билдов точка для %s уже снята." % screen)
        print("Переснять всё: py calibrate.py picks все")
        return

    prof = core.read_json(core.screen_path(screen))
    if list(prof.get("resolution") or []) != [core.SCREEN_W, core.SCREEN_H]:
        print("Экран %dx%d, а профиль screens/%s.json рассчитан на %s."
              % (core.SCREEN_W, core.SCREEN_H, screen, prof.get("resolution")))
        print("Снимать в него нельзя — точки получатся неверными.")
        here = "%dx%d" % (core.SCREEN_W, core.SCREEN_H)
        if here in core.screen_files():
            print("Снять для нынешнего экрана: py calibrate.py picks %s" % here)
        else:
            print("Профиля под этот экран нет. Поменяй разрешение или "
                  "заведи screens/%s.json." % here)
        return
    print("Экран: %dx%d, профиль screens/%s.json"
          % (core.SCREEN_W, core.SCREEN_H, screen))
    print("Открой экран выбора героя. Наводи на героя и жми F8.")
    print("F9 — пропустить, ESC — выйти (снятое останется).")
    print("Порядок как на экране: сила, ловкость, интеллект, универсалы,")
    print("внутри ряда по имени. Веди мышь слева направо.")
    print("Снимаем %d из %d билдов.\n" % (len(todo), len(names)))
    shown = None
    for i, (name, title, have, attr) in enumerate(todo, 1):
        if attr != shown:
            shown = attr
            cap = dict(ATTR_ORDER).get(attr, attr.upper())
            same = sum(1 for t in todo if t[3] == attr)
            print("=== %s (%d) ===" % (cap, same))
        print("[%d/%d] %s%s" % (i, len(todo), title,
                                "   (уже есть, переснимаем)" if have else ""))
        while True:
            if down(VK_ESC):
                wait_release(VK_ESC)
                print("\nвыход, снято %d" % (i - 1))
                return
            if down(VK_F9):
                wait_release(VK_F9)
                print("   пропущено\n")
                break
            if down(VK_F8):
                wait_release(VK_F8)
                xy = cursor()
                also = spread_pick(name, screen, xy)
                print("   %s%s\n"
                      % (xy, ("; пересчитано " + ", ".join(also))
                         if also else ""))
                break
            time.sleep(0.03)
    print("Готово. Проверь: py bot.py --check")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    keys = {k for k, _ in TARGETS}
    if "picks" in args:
        scr = [a for a in args if a in set(core.screen_files())]
        screen = scr[0] if scr else core.pick_screen(
            core.load_config().get("screen"))
        walk_picks(screen, only_missing="все" not in args)
        return
    screens = set(core.screen_files())
    heroes = set(core.hero_files())
    only = [a for a in args if a in keys]
    want_screen = [a for a in args if a in screens]
    want_hero = [a for a in args if a in heroes]
    junk = [a for a in args if a not in keys | screens | heroes]
    if junk:
        print("Не понял: %s" % ", ".join(junk))
        print("Точки:    %s" % ", ".join(k for k, _ in TARGETS))
        print("Профили:  %s" % ", ".join(sorted(screens)))
        print("Билды:    %s" % ", ".join(sorted(heroes)))
        return

    screen = want_screen[0] if want_screen else core.pick_screen(
        core.load_config().get("screen"))
    path = core.screen_path(screen)
    cfg = core.read_json(path)
    if not cfg:
        print("нет профиля screens/%s.json" % screen)
        return
    coords = cfg.setdefault("coords", {})
    targets = [t for t in TARGETS if t[0] in only] if only else TARGETS
    hero = want_hero[0] if want_hero else cur_hero()

    others = [x for x in core.screen_files() if x != screen]
    print("Экран: %dx%d, снимаю для профиля screens/%s.json"
          % (core.SCREEN_W, core.SCREEN_H, screen))
    if list(cfg.get("resolution") or []) != [core.SCREEN_W, core.SCREEN_H]:
        print("ВНИМАНИЕ: профиль рассчитан на %s, а экран сейчас другой. "
              "Снятое уедет — либо смени профиль, либо разрешение."
              % (cfg.get("resolution"),))
    if others:
        print("Остальные профили (%s) пересчитаю по формуле — но только "
              "там, где точка ещё не снята руками." % ", ".join(others))
    if any(k == "hero_pick" for k, _ in targets):
        print("Точка пика пойдёт в билд: heroes/%s.json  (сменить: "
              "py calibrate.py hero_pick <билд>)" % hero)
    print("F8 — записать, F9 — пропустить, ESC — выход.\n")

    for key, desc in targets:
        have = coords.get(key)
        print("-> %s" % desc)
        print("   ключ: %-22s %s" % (key, ("сейчас %s" % have) if have else "не задано"))
        while True:
            if down(VK_ESC):
                wait_release(VK_ESC)
                print("\nвыход")
                save(path, cfg)
                return
            if down(VK_F9):
                wait_release(VK_F9)
                print("   пропущено\n")
                break
            if down(VK_F8):
                wait_release(VK_F8)
                xy = cursor()
                if key == "hero_pick":
                    where, also = save_pick(xy, hero, screen)
                    print("   записано в %s (%s): %s%s\n"
                          % (where, screen, xy,
                             ("; пересчитано " + ", ".join(also))
                             if also else ""))
                else:
                    coords[key] = xy
                    measured(cfg, key)
                    also = spread(screen, key, xy)
                    print("   записано: %s%s\n"
                          % (xy, ("; пересчитано " + ", ".join(also))
                             if also else ""))
                break
            time.sleep(0.03)

    save(path, cfg)
    print("Готово. Проверь:  py bot.py --check")


def measured(prof, key):
    der = prof.setdefault("derived", [])
    if key in der:
        der.remove(key)
    prof["_derived"] = ("Точки, посчитанные по формуле из другого профиля, а "
                        "не снятые руками. Их можно пересчитывать заново; "
                        "снятое руками не трогается.")


def spread(from_screen, key, xy):
    src = core.read_json(core.screen_path(from_screen))
    w0 = (src.get("resolution") or [0])[0]
    done = []
    for other in core.screen_files():
        if other == from_screen:
            continue
        path = core.screen_path(other)
        prof = core.read_json(path)
        w1 = (prof.get("resolution") or [0])[0]
        if not w0 or not w1:
            continue
        co = prof.setdefault("coords", {})
        if key in co and key not in (prof.get("derived") or []):
            continue
        k = w1 / float(w0)
        co[key] = [int(round(xy[0] * k)), int(round(xy[1] * k))]
        der = prof.setdefault("derived", [])
        if key not in der:
            der.append(key)
        prof["derived"] = sorted(der)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(prof, f, ensure_ascii=False, indent=2)
        done.append("%s -> %s" % (other, co[key]))
    return done


def spread_pick(hero, from_screen, xy):
    path = core.hero_path(hero)
    h = core.read_json(path)
    at = h.get("pick_at")
    at = dict(at) if isinstance(at, dict) else {}
    derived = h.get("pick_derived") or []
    src = core.read_json(core.screen_path(from_screen))
    w0 = (src.get("resolution") or [0])[0]
    done = []
    for other in core.screen_files():
        if other == from_screen or not w0:
            continue
        prof = core.read_json(core.screen_path(other))
        w1 = (prof.get("resolution") or [0])[0]
        if not w1:
            continue
        if at.get(other) and other not in derived:
            continue
        k = w1 / float(w0)
        at[other] = [int(round(xy[0] * k)), int(round(xy[1] * k))]
        if other not in derived:
            derived.append(other)
        done.append("%s -> %s" % (other, at[other]))
    at[from_screen] = list(xy)
    if from_screen in derived:
        derived.remove(from_screen)
    h["pick_at"] = at
    h["pick_derived"] = sorted(derived)
    h["_pick_derived"] = ("Профили, где точка пика посчитана по формуле, а не "
                          "снята руками.")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=2)
    return done


def cur_hero():
    return core.load_config().get("hero", "")


def save_pick(xy, name, screen):
    if not core.read_json(core.hero_path(name or "")):
        return "никуда — билд %s не прочитан" % name, []
    also = spread_pick(name, screen, xy)
    return "heroes/%s.json" % name, also


def save(path, cfg):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("%s сохранён" % os.path.basename(path))


if __name__ == "__main__":
    main()
