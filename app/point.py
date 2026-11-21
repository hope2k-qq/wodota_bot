# -*- coding: utf-8 -*-
"""Где курсор. Наведи мышь на нужную точку в игре и подожди отсчёт.

    python -X utf8 point.py                 8 секунд, только печатает
    python -X utf8 point.py 15              отсчёт подольше
    python -X utf8 point.py 8 both          записать в билд: ПКМ и ЛКМ
    python -X utf8 point.py 8 move          только точку ПКМ
    python -X utf8 point.py 8 cast          только точку ЛКМ
    python -X utf8 point.py 8 stash         кнопка «в тайник» в профиль экрана

move, cast и both пишутся в reactions.wave_move выбранного героя
(config.json, hero), stash — в coords.stash_button профиля экрана
screens/<ШхВ>.json. Файлы правятся текстом, а не пересборкой json: так
остаются как есть и порядок полей, и пояснения, и переводы строк.
"""
import ctypes
import io
import json
import os
import sys
import time

import core

HERE = core.HERE


class P(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def at():
    p = P()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def point_text(x, y, pad):
    nl = chr(10)
    return ("[" + nl + " " * (pad + 2) + str(x) + "," + nl
            + " " * (pad + 2) + str(y) + nl + " " * pad + "]")


def set_value(text, key, x, y, pad):
    head = '"' + key + '"'
    i = text.find(head)
    if i < 0:
        return None
    j = text.find(":", i + len(head))
    if j < 0:
        return None
    k = j + 1
    while k < len(text) and text[k] in " \t":
        k += 1
    if text[k] == "[":
        end = text.find("]", k) + 1
    elif text[k:k + 4] == "null":
        end = k + 4
    else:
        return None
    return text[:k] + point_text(x, y, pad) + text[end:]


def save_hero(keys, x, y):
    hero = json.load(io.open(core.file("config.json"),
                             encoding="utf-8")).get("hero", "")
    path = core.hero_path(hero)
    if not hero or not os.path.exists(path):
        print("не нашёл билд героя (config.json, hero = %r)" % hero)
        return
    s = io.open(path, encoding="utf-8", newline="").read()
    if '"wave_move"' not in s:
        print("в билде %s нет блока reactions.wave_move" % hero)
        return
    for key in keys:
        out = set_value(s, key, x, y, 6)
        if out is None:
            print("в билде нет поля %r (или у него чужое значение)" % key)
            return
        s = out
    io.open(path, "w", encoding="utf-8", newline="").write(s)
    w = json.load(io.open(path, encoding="utf-8"))["reactions"]["wave_move"]
    print("записал в heroes/%s.json:  move = %s,  cast = %s"
          % (hero, w["move"], w["cast"]))


def save_screen(key, x, y):
    w = ctypes.windll.user32.GetSystemMetrics(0)
    h = ctypes.windll.user32.GetSystemMetrics(1)
    path = core.screen_path("%dx%d" % (w, h))
    if not os.path.exists(path):
        print("нет профиля экрана %s" % path)
        return
    s = io.open(path, encoding="utf-8", newline="").read()
    out = set_value(s, key, x, y, 4)
    if out is None:
        print("в профиле %dx%d нет поля %r" % (w, h, key))
        return
    io.open(path, "w", encoding="utf-8", newline="").write(out)
    d = json.load(io.open(path, encoding="utf-8"))
    print("записал в screens/%dx%d.json:  %s = %s" % (w, h, key, d["coords"][key]))


TARGET = {"both": ["move", "cast"], "move": ["move"], "cast": ["cast"],
          "stash": []}


def main():
    wait = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    what = sys.argv[2].lower() if len(sys.argv) > 2 else ""
    keys = TARGET.get(what)
    if what and keys is None:
        print("второй аргумент: both, move, cast или stash")
        return 1
    print("Наведи курсор на нужную точку. Осталось:")
    for i in range(wait, 0, -1):
        x, y = at()
        sys.stdout.write("\r  %2d c   курсор %d,%d          " % (i, x, y))
        sys.stdout.flush()
        time.sleep(1)
    x, y = at()
    print("\n\nточка: [%d, %d]" % (x, y))
    if what == "stash":
        save_screen("stash_button", x, y)
    elif keys:
        save_hero(keys, x, y)
    return 0


if __name__ == "__main__":
    sys.exit(main())
