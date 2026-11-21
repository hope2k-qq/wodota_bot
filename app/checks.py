"""
Проверки перед стартом: что бот поправил сам и что нужно от человека.

Всё, что можно сделать за пользователя, делается молча: конфиг GSI
кладётся на место, пароли придумываются сами. Остальное — то, что бот
изменить не может, — собирается в короткий список и показывается при
запуске и на пульте, пока не исправлено.

    py app/checks.py        напечатать список прямо сейчас
"""

import glob
import io
import os
import re
import secrets
import sys

import core
import machine

APPID = "570"
NEED_OPTIONS = ["-gamestateintegration", "-console"]


def read(path):
    try:
        return io.open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def launch_options():
    root = machine.steam_dir()
    if not root:
        return None
    best = None
    for path in glob.glob(os.path.join(root, "userdata", "*", "config",
                                       "localconfig.vdf")):
        text = read(path)
        for m in re.finditer(r'"LaunchOptions"\s*"([^"]*)"', text, re.I):
            best = m.group(1)
    return best


def window_mode():
    root = machine.steam_dir()
    if not root:
        return None
    for path in glob.glob(os.path.join(root, "userdata", "*", APPID,
                                       "local", "cfg", "video.txt")):
        text = read(path)
        if not text:
            continue
        def val(key):
            m = re.search(r'"setting\.%s"\s*"([^"]*)"' % key, text)
            return m.group(1) if m else None
        full, border = val("fullscreen"), val("nowindowborder")
        if full is None:
            continue
        return {"fullscreen": full == "1", "borderless": border == "1"}
    return None


def gsi_path():
    d = machine.dota_state()
    if not d.get("ok") or not d.get("dir"):
        return ""
    return os.path.join(d["dir"], "game", "dota", "cfg",
                        "gamestate_integration",
                        "gamestate_integration_wodota.cfg")


def gsi_cfg(fix=True):
    src = core.file("gamestate_integration_wodota.cfg")
    if not os.path.exists(src):
        return False, ("нет файла data/gamestate_integration_wodota.cfg — "
                       "без него Dota не шлёт данные и бот слеп")
    dst = gsi_path()
    if not dst:
        return False, ("не нашёл папку Dota — положи "
                       "data/gamestate_integration_wodota.cfg в "
                       "steamapps/common/dota 2 beta/game/dota/cfg/"
                       "gamestate_integration/ и перезапусти игру")
    there = os.path.exists(dst)
    if there and open(dst, "rb").read() == open(src, "rb").read():
        return True, ""
    if not fix:
        return False, "конфиг GSI не на месте"
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        import shutil
        shutil.copyfile(src, dst)
    except Exception as e:
        return False, "конфиг GSI не скопировался (%s) — положи руками" % e
    return False, ("конфиг GSI %s, я вернул его на место. Dota читает его "
                   "только при запуске — перезапусти игру"
                   % ("отличался" if there else "пропал"))


def party_token(fix=True):
    conf = core.load_config()
    party = conf.get("party") or {}
    if party.get("token"):
        return True, ""
    if not fix:
        return False, "пароль пати не задан"
    data = core.read_json(core.LOCAL_FILE) or {}
    data.setdefault("party", {})["token"] = secrets.token_hex(8)
    try:
        with io.open(core.LOCAL_FILE, "w", encoding="utf-8") as f:
            import json
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        return False, "пароль пати не записался: %s" % e
    return True, ("пароль пати придуман и записан в settings/local.json — "
                  "перенеси тот же пароль на второй ПК")


def build_ready(conf):
    name = conf.get("hero_file") or conf.get("hero") or ""
    plan = (conf.get("talents") or {}).get("plan") or []
    if name == "standard":
        return False, ("выбран образец standard — сделай билд под своего "
                       "героя: на пульте «Настройки», раздел «Герои», "
                       "кнопка «Новый билд»")
    bits = []
    if not conf.get("gsi_name"):
        bits.append("не задан герой")
    if not plan:
        bits.append("пустой план талантов")
    if not bits:
        return True, ""
    return False, "в билде %s %s" % (name, " и ".join(bits))


def hero_point(conf):
    name = conf.get("hero_file") or conf.get("hero") or ""
    scr = conf.get("screen_file") or ""
    at = conf.get("pick_at")
    if isinstance(at, dict):
        at = at.get(scr)
    if at and any(at):
        return True, ""
    return False, ("у билда %s не снята точка пика для %s — открой на пульте "
                   "«Билд» и нажми «Снять координату»" % (name or "?", scr))


def screen_profile(conf):
    want = list(conf.get("resolution") or [])
    now = [core.SCREEN_W, core.SCREEN_H]
    if want == now:
        return True, ""
    return False, ("профиль экрана %s рассчитан на %s, а экран сейчас %dx%d — "
                   "координаты не совпадут. Сними их заново или добавь свой "
                   "профиль в screens/"
                   % (conf.get("screen_file", "?"), want or "?", now[0], now[1]))


def dota_options():
    opts = launch_options()
    if opts is None:
        return True, ""
    miss = [o for o in NEED_OPTIONS if o not in opts]
    if not miss:
        return True, ""
    return False, ("в параметрах запуска Dota нет %s — добавь в Steam: "
                   "правой кнопкой по Dota 2, Свойства, строка параметров: "
                   "+fps_max 0 -console -gamestateintegration"
                   % " и ".join(miss))


def dota_window():
    mode = window_mode()
    if mode is None:
        return True, ""
    if not mode["fullscreen"] and mode["borderless"]:
        return True, ""
    return False, ("Dota стоит не в режиме «окно без рамки» — в полный экран "
                   "она меняет разрешение рабочего стола, и снятые "
                   "координаты уедут. Поменяй в настройках игры")


def party_ready(conf):
    party = conf.get("party") or {}
    if not party.get("on"):
        return []
    out = []
    if (party.get("role") or "main") == "main" and not party.get("invite_id"):
        out.append((False, "пати включена, но не задан Dota ID напарника — "
                           "впиши его на пульте, страница «Пати»"))
    co = conf.get("coords") or {}
    want = (("party_field", "party_invite") if party.get("role") != "second"
            else ("party_accept",))
    miss = [k for k in want if not co.get(k)]
    if miss:
        out.append((False, "для пати не сняты точки %s — сними их на пульте, "
                           "страница «Координаты экрана»" % ", ".join(miss)))
    return out


def checklist(conf=None, fix=True):
    conf = conf or core.load_config()
    out = [("gsi",) + gsi_cfg(fix),
           ("party_token",) + party_token(fix),
           ("options",) + dota_options(),
           ("window",) + dota_window(),
           ("screen",) + screen_profile(conf),
           ("build",) + build_ready(conf),
           ("hero",) + hero_point(conf)]
    out += [("party",) + x for x in party_ready(conf)]
    return [(key, ok, note) for key, ok, note in out if note]


def main():
    core.log("проверка настроек")
    items = checklist()
    if not items:
        print("всё на месте, можно запускать")
        return 0
    for _, ok, note in items:
        print(("  сделал: " if ok else "  нужно: ") + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
