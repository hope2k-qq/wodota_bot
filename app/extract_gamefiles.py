"""
Разовая выгрузка файлов Dota в gamefiles/. Бот её не вызывает.

Цены и названия предметов лежат внутри pak01_dir.vpk. Лазить туда на
каждом запуске незачем: нужные два файла достаются один раз и дальше
читаются как обычные. Перезапускать стоит после патча Dota, если цены
разъехались.

    py extract_gamefiles.py
"""

import json
import os
import re
import struct

TARGETS = {
    "scripts/npc/items.txt": "items_dota.txt",
    "resource/localization/abilities_english.txt": "items_names.txt",
}
HEROES_DIR_IN_PAK = "scripts/npc/heroes/"
ATTACK_OUT = "heroes_attack.json"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gamefiles")


def steam_dir():
    try:
        import winreg
    except ImportError:
        return ""
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for key, val in ((r"Software\Valve\Steam", "SteamPath"),
                         (r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    v = winreg.QueryValueEx(k, val)[0]
                if v and os.path.isdir(v):
                    return v
            except OSError:
                continue
    return ""


def cstr(f):
    out = bytearray()
    while True:
        c = f.read(1)
        if not c or c == b"\0":
            break
        out += c
    return out.decode("utf-8", "replace")


def walk(path):
    with open(path, "rb") as f:
        sig, ver = struct.unpack("<II", f.read(8))
        if sig != 0x55AA1234:
            raise ValueError("это не VPK")
        f.read(4)
        if ver == 2:
            f.read(16)
        while True:
            ext = cstr(f)
            if not ext:
                return
            while True:
                folder = cstr(f)
                if not folder:
                    break
                while True:
                    name = cstr(f)
                    if not name:
                        break
                    _crc, pre_len, arch, off, length = struct.unpack(
                        "<IHHII", f.read(16))
                    f.read(2)
                    pre = f.read(pre_len) if pre_len else b""
                    full = ("%s/%s.%s" % (folder, name, ext)
                            if folder != " " else "%s.%s" % (name, ext))
                    yield full, (arch, off, length, pre)


def grab(path, entry):
    arch, off, length, pre = entry
    if not length:
        return pre
    with open("%s_%03d.vpk" % (path.replace("_dir.vpk", ""), arch), "rb") as a:
        a.seek(off)
        return pre + a.read(length)


def as_text(data):
    if data[1:2] == b"\0" or data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16-le", "replace").lstrip("﻿")
    return data.decode("utf-8", "replace")


def main():
    root = steam_dir()
    if not root:
        print("Steam не найден в реестре")
        return
    vpk = os.path.join(root, "steamapps", "common", "dota 2 beta",
                       "game", "dota", "pak01_dir.vpk")
    if not os.path.exists(vpk):
        print("нет пака:", vpk)
        return
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
    left = dict(TARGETS)
    attack = {}
    for full, entry in walk(vpk):
        if full.startswith(HEROES_DIR_IN_PAK) and full.endswith(".txt"):
            name = os.path.basename(full)[:-4]
            if name.startswith("npc_dota_hero_"):
                txt = as_text(grab(vpk, entry))
                m = re.search(r'"AttackCapabilities"\s+"([^"]+)"', txt)
                a = re.search(r'"AttributePrimary"\s+"([^"]+)"', txt)
                if m or a:
                    attack[name] = {
                        "attack": ("ranged" if m and "RANGED" in m.group(1)
                                   else "melee"),
                        "attr": ((a.group(1).replace("DOTA_ATTRIBUTE_", "")
                                  .lower()) if a else "all"),
                    }
            continue
        if full not in left:
            continue
        text = as_text(grab(vpk, entry))
        dst = os.path.join(OUT_DIR, left.pop(full))
        with open(dst, "w", encoding="utf-8") as f:
            f.write(text)
        print("%-46s -> gamefiles/%s  (%d символов)"
              % (full, os.path.basename(dst), len(text)))
    for missing in left:
        print("НЕ НАЙДЕНО в паке:", missing)
    if attack:
        dst = os.path.join(OUT_DIR, ATTACK_OUT)
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(attack, f, ensure_ascii=False, indent=1, sort_keys=True)
        print("%-46s -> gamefiles/%s  (%d героев)"
              % (HEROES_DIR_IN_PAK + "*.txt", ATTACK_OUT, len(attack)))
    print("\nдальше: py gamedata.py")


if __name__ == "__main__":
    main()
