"""
Распаковка файлов самой аркады из подписки воркшопа. Бот её не вызывает.

Из vpk аркады достаются только текстовые справочники: `scripts/npc`,
`resource/addon_*.txt` и список талантов. Из них потом собираются
`gamedata.json` (предметы, цены, герои) и сетка талантов.

Запускать после обновления аркады:

    py extract_addon.py                в папку из addon_dir
    py extract_addon.py C:\\hope2k\\custom_5    в свою папку

Дальше: py gamedata.py
"""

import os
import re
import struct
import sys

import core
import machine

WANT_DIRS = ("scripts/npc", "resource")
WANT_FILES = ("scripts/vscripts/configs/talents_list.lua",)


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
        tree = struct.unpack("<I", f.read(4))[0]
        head = 12
        if ver == 2:
            f.read(16)
            head = 28
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
                    yield full, (arch, off, length, pre, head + tree)


def grab(path, entry):
    arch, off, length, pre, base = entry
    if not length:
        return pre
    if arch == 0x7FFF:
        with open(path, "rb") as f:
            f.seek(base + off)
            return pre + f.read(length)
    with open("%s_%03d.vpk" % (path.replace("_dir.vpk", ""), arch), "rb") as a:
        a.seek(off)
        return pre + a.read(length)


def wanted(full):
    if full in WANT_FILES:
        return True
    folder = full.rsplit("/", 1)[0]
    return folder in WANT_DIRS and full.endswith((".txt", ".lua"))


def addon_vpk(wid):
    root = machine.steam_dir()
    if not root:
        return "", "Steam не найден в реестре"
    path = os.path.join(root, "steamapps", "workshop", "content",
                        machine.DOTA_APPID, wid, "%s.vpk" % wid)
    if not os.path.exists(path):
        return "", "нет файла аркады: %s" % path
    return path, ""


def main():
    cfg = core.read_json(core.file("config.json")) or {}
    out = (sys.argv[1] if len(sys.argv) > 1
           else cfg.get("addon_dir") or r"C:\hope2k\custom_4")
    cmd = (cfg.get("launch") or {}).get("command", "")
    m = re.search(r"\d{6,}", cmd)
    wid = m.group(0) if m else ""
    if not wid:
        print("в launch.command нет номера кастомки")
        return
    vpk, why = addon_vpk(wid)
    if not vpk:
        print(why)
        return

    state = machine.addon_state(wid)
    if state.get("ok") and not state.get("fresh"):
        print("ВНИМАНИЕ: Steam ещё не докачал свежую версию аркады")

    n = 0
    for full, entry in walk(vpk):
        if not wanted(full):
            continue
        data = grab(vpk, entry)
        dst = os.path.join(out, full.replace("/", os.sep))
        folder = os.path.dirname(dst)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with open(dst, "wb") as f:
            f.write(data)
        n += 1
        print("%-56s %8d байт" % (full, len(data)))
    print("файлов: %d -> %s" % (n, out))
    print("дальше: py gamedata.py")


if __name__ == "__main__":
    main()
