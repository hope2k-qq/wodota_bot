"""
Управление машиной и игрой: состояние обновлений, перезапуск Dota,
выключение компьютера.

Вынесено отдельно от пульта, потому что к боту отношения не имеет и
проверяется само по себе: `py machine.py` печатает состояние и ничего
не трогает.
"""

import ctypes
import io
import os
import re
import subprocess
import sys
import time

import core
from core import log

STEAM_KEYS = [
    (r"Software\Valve\Steam", "SteamPath"),
    (r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
]
DOTA_APPID = "570"
WM_CLOSE = 0x0010


def steam_dir():
    try:
        import winreg
    except ImportError:
        return ""
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for path, name in STEAM_KEYS:
            try:
                with winreg.OpenKey(hive, path) as k:
                    v = winreg.QueryValueEx(k, name)[0]
                if v and os.path.isdir(v):
                    return os.path.normpath(v)
            except OSError:
                continue
    return ""


def kv(text, key):
    m = re.search(r'"%s"\s+"([^"]*)"' % re.escape(key), text)
    return m.group(1) if m else ""


def read_text(path):
    try:
        return io.open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def when(stamp):
    try:
        n = int(stamp)
    except (TypeError, ValueError):
        return ""
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(n)) if n else ""


def library_dirs():
    root = steam_dir()
    if not root:
        return []
    out = [root]
    seen = {os.path.normcase(os.path.normpath(root))}
    txt = read_text(os.path.join(root, "steamapps", "libraryfolders.vdf"))
    for m in re.finditer(r'"path"\s*"([^"]+)"', txt):
        path = os.path.normpath(m.group(1).replace("\\\\", "\\"))
        key = os.path.normcase(path)
        if os.path.isdir(path) and key not in seen:
            seen.add(key)
            out.append(path)
    return out


def dota_home():
    for lib in library_dirs():
        acf = os.path.join(lib, "steamapps",
                           "appmanifest_%s.acf" % DOTA_APPID)
        game = os.path.join(lib, "steamapps", "common", "dota 2 beta")
        if os.path.exists(acf) or os.path.isdir(game):
            return acf, game
    root = steam_dir()
    if not root:
        return "", ""
    return (os.path.join(root, "steamapps",
                         "appmanifest_%s.acf" % DOTA_APPID),
            os.path.join(root, "steamapps", "common", "dota 2 beta"))


def dota_state():
    root = steam_dir()
    if not root:
        return {"ok": False, "why": "Steam не найден в реестре"}
    acf, game = dota_home()
    txt = read_text(acf)
    if not txt:
        return {"ok": False, "why": "нет appmanifest_%s.acf" % DOTA_APPID}
    try:
        flags = int(kv(txt, "StateFlags") or 0)
    except ValueError:
        flags = 0
    return {
        "ok": True,
        "build": kv(txt, "buildid"),
        "updated": when(kv(txt, "LastUpdated")),
        "flags": flags,
        "need_update": bool(flags & 2),
        "updating": bool(flags & 1024),
        "dir": game,
    }


def addon_state(workshop_id):
    root = steam_dir()
    if not root:
        return {"ok": False, "why": "Steam не найден"}
    acf = os.path.join(root, "steamapps", "workshop",
                       "appworkshop_%s.acf" % DOTA_APPID)
    txt = read_text(acf)
    if not txt:
        return {"ok": False, "why": "нет appworkshop_%s.acf" % DOTA_APPID}
    i = txt.find('"WorkshopItemDetails"')
    j = txt.find('"%s"' % workshop_id, i if i >= 0 else 0)
    if j < 0:
        return {"ok": False, "why": "кастомки %s нет в подписках" % workshop_id}
    block = txt[j:txt.find("\n\t\t}", j) + 4]
    have, latest = kv(block, "manifest"), kv(block, "latest_manifest")
    return {
        "ok": True,
        "id": workshop_id,
        "have": have,
        "latest": latest,
        "fresh": bool(latest) and have == latest,
        "updated": when(kv(block, "timeupdated")),
        "latest_updated": when(kv(block, "latest_timeupdated")),
    }


def dota_running():
    return bool(core.find_dota())


def close_dota(timeout=12):
    h = core.find_dota()
    if not h:
        return True
    core.user32.PostMessageW(h, WM_CLOSE, 0, 0)
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.5)
        if not core.find_dota():
            return True
    log("Dota не закрылась за %d с — закрываю принудительно" % timeout)
    subprocess.run(["taskkill", "/IM", "dota2.exe", "/F"],
                   capture_output=True)
    end = time.time() + 10
    while time.time() < end:
        time.sleep(0.5)
        if not core.find_dota():
            return True
    return False


def start_dota(wait=180):
    try:
        os.startfile("steam://rungameid/%s" % DOTA_APPID)
    except Exception as e:
        log("не удалось позвать Steam: %s" % e)
        return False
    end = time.time() + wait
    while time.time() < end:
        time.sleep(1)
        if core.find_dota():
            return True
    return False


def restart_dota():
    st = dota_state()
    pending = st.get("need_update") or st.get("updating")
    log("перезапуск Dota%s" % (" (Steam ждёт обновления)" if pending else ""))
    if not close_dota():
        return "Dota не закрылась"
    log("Dota закрыта, зову Steam")
    if not start_dota():
        return "Dota не поднялась — проверь Steam"
    return "Dota перезапущена"


def shutdown(delay=10):
    log("ВЫКЛЮЧЕНИЕ КОМПЬЮТЕРА через %d с" % delay)
    r = subprocess.run(["shutdown", "/s", "/t", str(delay),
                        "/c", "WoDOTA: выключение с пульта"],
                       capture_output=True, text=True)
    if r.returncode:
        return "не вышло: %s" % (r.stderr or r.stdout or r.returncode)
    return "компьютер выключится через %d с" % delay


def cancel_shutdown():
    r = subprocess.run(["shutdown", "/a"], capture_output=True, text=True)
    return "выключение отменено" if not r.returncode else "отменять нечего"


def main():
    cfg = core.load_config()
    wid = re.search(r"\d{6,}", (cfg.get("launch") or {}).get("command", ""))
    d = dota_state()
    print("Steam:", steam_dir() or "не найден")
    if d["ok"]:
        print("Dota: сборка %s, обновлена %s" % (d["build"], d["updated"]))
        print("      запущена: %s" % ("да" if dota_running() else "нет"))
        print("      обновление ждёт: %s" % ("да" if d["need_update"] else "нет"))
    else:
        print("Dota:", d["why"])
    if wid:
        a = addon_state(wid.group(0))
        if a["ok"]:
            print("Кастомка %s:" % a["id"])
            print("      скачана %s" % a["updated"])
            print("      на сервере %s" % (a["latest_updated"] or "?"))
            print("      актуальна: %s" % ("да" if a["fresh"] else "НЕТ"))
        else:
            print("Кастомка:", a["why"])


if __name__ == "__main__":
    main()
