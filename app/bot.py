"""
Авто-игра WoDOTA: зайти в кастомку -> раскидать таланты -> купить предметы -> повторить.

Боёвка не автоматизируется.

    py bot.py            рабочий режим
    py bot.py --dry      прогон без кликов: мышь обходит все ячейки плана
    py bot.py --check    проверить конфиг и показать посчитанные координаты

Аварийный стоп — F12 в любой момент.
"""

import os
import collections
import json
import random
import re
import shutil
import sys
import threading
import time
import traceback

import checks
import core
import machine
import party
from core import log

def build_late(role_items):
    roles = cfg.get("roles") or {}
    bkb = roles.get("bkb") or {}
    once = roles.get("use_once") or {}
    boss = roles.get("boss_swap") or {}
    out = dict(cfg.get("shop") or {})
    out.pop("order", None)
    out["order"] = role_items
    out["swap_slot"] = bkb.get("slot", 4)
    out["boss_slot"] = boss.get("slot", 6)
    out["boss_wait_s"] = boss.get("wait_s", 5)
    out["bkb_key"] = bkb.get("key", ["v"])
    out["moon_key"] = once.get("key", ["alt", "v"])
    out["wave_delay_s"] = bkb.get("wave_delay_s", 1)
    out["wave_delay_by_unit"] = bkb.get("wave_delay_by_unit") or {}
    out["bkb_max_late_s"] = bkb.get("max_late_s", 8)
    for e in role_items:
        if e.get("role") == "bkb":
            out.setdefault("bkb_item", e["name"])
        elif e.get("role") == "boss_swap":
            out.setdefault("boss_item", e["name"])
            if e.get("slot"):
                out["boss_slot"] = e["slot"]
            out.setdefault("boss_items", []).append(
                {"name": e["name"],
                 "slot": e.get("slot") or boss.get("slot", 6)})
    return out


def config_sign(d):
    return json.dumps(d, sort_keys=True, ensure_ascii=False)


def bind_config(new):
    global cfg, T, AB, END, WATCH, WAVE, BKB, REACT, LATE, ITEMS, CO, FUNCS
    global CONFIG_SIGN, ROLES
    CONFIG_SIGN = config_sign(new)
    cfg = new
    T = cfg["talents"]
    AB = cfg["abilities"]
    WATCH = cfg.get("watchdog", {})
    WAVE = cfg.get("waves", {})
    BKB = cfg.get("bkb", {})
    REACT = cfg.get("reactions", {})
    ROLES = cfg.get("roles") or {}
    globals()["BAR"] = cfg.get("wave_bar") or {}
    plain, role_items, books = core.resolve_items(cfg)
    ITEMS = dict(cfg.get("shop") or {})
    ITEMS["order"] = plain
    LATE = build_late(role_items)
    END = dict(books or {})
    END.setdefault("search", "")
    END.setdefault("cost", 0)
    END["keep_gold"] = (ROLES.get("books") or {}).get("keep_gold", 0)
    END["max_per_visit"] = (ROLES.get("books") or {}).get("max_per_visit", 6)
    END["buy_delay_ms"] = ITEMS.get("buy_delay_ms", 500)
    CO = cfg["coords"]
    CO["hero_pick"] = cfg.get("pick_at") or []
    lst = cfg.get("difficulties") or []
    n = int(cfg.get("difficulty", 0) or 0)
    if 1 <= n <= len(lst):
        CO["difficulty_pick"] = lst[n - 1].get("at")
    FUNCS = cfg.get("functions", {})


CREEPS = core.load_creeps()
bind_config(core.load_config())


def on(name):
    return bool(FUNCS.get(name, True))

TREE_NAME = {1: "сила", 2: "ловкость", 3: "интеллект"}



def cell_xy(tree, row, col):
    x = T["tree_x"][tree - 1] + T["col_pitch"] * (col - 1)
    y = T["row_y0"] + T["row_pitch"] * (row - 1)
    return x, y


def plan_steps():
    cells = []
    for e in T["plan"]:
        cells.extend([tuple(e[:3])] * (e[3] if len(e) > 3 else 1))
    total = {}
    for c in cells:
        total[c] = total.get(c, 0) + 1
    out, seen = [], {}
    for c in cells:
        seen[c] = seen.get(c, 0) + 1
        out.append((c[0], c[1], c[2], seen[c], total[c]))
    return out


STEPS = plan_steps()
BAR = {}



class Abort(Exception):
    pass


class Fatal(Exception):
    pass


def watchdog(gsi=None):
    if not core.find_dota():
        raise Fatal("окно Dota пропало — игра закрыта или упала")
    if gsi is not None:
        limit = WATCH.get("gsi_silence_s", 90)
        if gsi.age > limit:
            raise Fatal("от игры нет данных %.0f с (предел %d) — "
                        "зависла или потеряна сеть" % (gsi.age, limit))


def guard():
    if core.abort_pressed():
        raise Abort("нажат F12")


def countdown(seconds=None, why="Переключись в Dota"):
    seconds = cfg.get("startup_delay_s", 5) if seconds is None else seconds
    print("%s. Старт через:" % why, end=" ", flush=True)
    for i in range(seconds, 0, -1):
        if core.abort_pressed():
            print("\nотменено")
            return False
        print("%d" % i, end=" ", flush=True)
        time.sleep(1)
    print("- поехали\n", flush=True)
    return True


def need(*keys):
    missing = [k for k in keys if not CO.get(k)]
    if missing:
        raise Abort("в config.json не заполнены координаты: " + ", ".join(missing)
                    + "   -> запусти calibrate.py")



def tree_mask(tree):
    return core.white_mask(T["tree_counter_region"][tree - 1])


def talent_button():
    hunt = T.get("hunt") or {}
    fixed = CO.get("talent_panel_toggle")
    if not hunt.get("strip"):
        return fixed
    tpl = core.template(hunt.get("template", "hud_drop.png"),
                        hunt.get("scale", 1.0))
    found = core.find_patch(tpl, hunt["strip"])
    ok = found and found[2] <= hunt.get("max_diff", 20)
    dx, dy = hunt.get("offset", [0, 0])
    where = [found[0] + dx, found[1] + dy] if ok else fixed
    if where != globals().get("_leaf_at"):
        globals()["_leaf_at"] = where
        if ok:
            log("кнопка талантов: %s (нашёл по образцу, расхождение %.1f)"
                % (where, found[2]))
        else:
            log("кнопка талантов: образец не нашёлся%s — беру из профиля %s"
                % ("" if not found else " (расхождение %.1f)" % found[2], where))
    return where


def toggle_panel(open_it):
    need("talent_panel_toggle")
    core.focus_dota()
    if open_it or not globals().get("_leaf_at"):
        where = talent_button()
    else:
        where = globals()["_leaf_at"]
    core.click(*where)
    time.sleep(T.get("panel_settle_s", 0.7))


def nap(seconds, why=""):
    if seconds <= 0:
        return
    if why:
        log("жду %.0f с: %s" % (seconds, why))
    end = time.time() + seconds
    while time.time() < end:
        guard()
        time.sleep(0.2)


def allowed_steps(gsi):
    limit = T.get("phase1_count", len(STEPS))
    if limit >= len(STEPS):
        return len(STEPS)
    clock = gsi.get("map.clock_time", 0) or 0
    return len(STEPS) if clock >= T.get("phase2_after_s", 600) else limit


SERVICE_UNITS = ("npc_dota_thinker",)


def enemy_creeps(gsi):
    d, _ = gsi.snapshot()
    out = []
    for v in (d.get("minimap") or {}).values():
        name = v.get("unitname") or ""
        if v.get("team") != 4 or not name or name in SERVICE_UNITS:
            continue
        out.append(v)
    return out


def creep_name(unitname):
    if unitname in CREEPS:
        return CREEPS[unitname]
    if unitname.startswith("npc_dota_hero_"):
        return "герой " + unitname[len("npc_dota_hero_"):]
    return unitname.replace("npc_woda_", "").replace("_pve", "")


BAR_REF = {"fight": (0x4D, 0x80, 0x8A),
           "relax": (0x74, 0x82, 0x2E),
           "boss":  (0x6B, 0x31, 0x24)}


def bar_look():
    box = BAR.get("box")
    if not box:
        return None, None, None, None
    c = core.avg_color(box)
    dist = lambda k: sum((a - b) ** 2 for a, b in zip(c, BAR_REF[k])) ** 0.5
    best = min(BAR_REF, key=dist)
    d = dist(best)
    return (best if d <= BAR.get("tol", 45) else None), c, best, d


def bar_state():
    return bar_look()[0]


def bar_watcher(gsi, st, stop):
    if not (REACT.get("ult") or {}).get("by_bar"):
        return
    if not BAR.get("box"):
        log("дозор за полосой: участок не задан (wave_bar в профиле) — не слежу")
        return
    log("дозор за полосой: слежу за %s, опрос раз в %g с"
        % (BAR["box"], BAR.get("poll_s", 0.06)))
    was, blind = None, None
    while not stop.wait(BAR.get("poll_s", 0.06)):
        try:
            now, c, near, d = bar_look()
            if now is None:
                if blind is None:
                    blind = time.time()
                elif blind and time.time() - blind > BAR.get("blind_s", 20):
                    blind = 0
                    log("дозор за полосой: цвета не узнаю уже %g с — вижу "
                        "%02X%02X%02X, ближе всего %s (расхождение %.0f, "
                        "допуск %s). Снимок: %s"
                        % (BAR.get("blind_s", 20), c[0], c[1], c[2], near, d,
                           BAR.get("tol", 45),
                           core.save_shot("bar.jpg", [0, 0, core.SCREEN_W, 140])))
                continue
            blind = None
            if now == was:
                continue
            was, st["bar_kind"] = now, now
            if not st.get("bar_said"):
                st["bar_said"] = True
                log("дозор за полосой: цвет опознан (%s, расхождение %.0f) — "
                    "работаю по полосе" % (now, d))
            if now != "fight":
                continue
            st["bar_at"] = time.time()
            if on("ult") and (REACT.get("ult") or {}).get("by_bar", True):
                cast_by_bar(gsi, st)
        except Exception as e:
            log("дозор за полосой: %s: %s" % (type(e).__name__, e))
            return


def fast_watcher(gsi, st, stop):
    cfg_u = REACT.get("ult") or {}
    if not on("ult") or not cfg_u.get("fast", True):
        return
    log("быстрый дозор: слежу за пакетами GSI, опрос раз в %g с"
        % cfg_u.get("fast_s", 0.05))
    prev, last_clock, stamp0 = {}, -999, 0.0
    while not stop.wait(cfg_u.get("fast_s", 0.05)):
        try:
            d, stamp = gsi.snapshot()
            if stamp == stamp0 or not d:
                continue
            stamp0 = stamp
            clock = ((d.get("map") or {}).get("clock_time")) or 0
            now = {}
            for v in (d.get("minimap") or {}).values():
                u = v.get("unitname") or ""
                if v.get("team") != 4 or not u or u in SERVICE_UNITS:
                    continue
                if starts_wave(u):
                    now[u] = now.get(u, 0) + 1
            jump = {u: now[u] - prev.get(u, 0) for u in now}
            prev = now
            boss = any(is_boss(u) for u in jump if jump[u] > 0)
            big = [u for u in jump
                   if not is_boss(u) and jump[u] >= WAVE.get("spawn_count", 3)]
            if boss and not cfg_u.get("on_boss", True):
                last_clock = clock
                continue
            if not big or clock - last_clock < WAVE.get("min_gap_s", 5):
                continue
            last_clock = clock
            wave_f1(st, "пакету")
            wait = cfg_u.get("after_s", 0)
            if wait > 0:
                time.sleep(wait)
            do_cast(gsi, st, by="пакету")
        except Exception as e:
            log("быстрый дозор: %s: %s" % (type(e).__name__, e))
            return


def cast_by_bar(gsi, st):
    cfg_u = REACT.get("ult") or {}
    wait = cfg_u.get("after_s", 0.2)
    if wait > 0:
        time.sleep(wait)
    if bar_state() != "fight":
        return
    do_cast(gsi, st, by="полосе")


def wave_watcher(gsi, st, stop):
    while not stop.wait(WAVE.get("poll_s", 1.0)):
        try:
            track_waves(gsi, st)
            why = party_lost(st)
            if why:
                st["party_lost"] = why
                return
            wave_f1(st, "волне")
            track_bkb(gsi, st)
            track_death(gsi, st)
            track_satanic(gsi, st)
            track_disarm(gsi, st)
            maybe_bkb(gsi, st)
            maybe_boss_swap(gsi, st)
            maybe_wave_move(gsi, st)
            maybe_ult(gsi, st)
            maybe_boss_cast(gsi, st)
        except Exception as e:
            log("наблюдатель: %s: %s" % (type(e).__name__, e))
            return


def acquire_if(check, what, timeout=None):
    if timeout is None:
        core.INPUT_LOCK.acquire()
    elif not core.INPUT_LOCK.acquire(timeout=timeout):
        return False
    if check is not None and not check():
        core.INPUT_LOCK.release()
        log("%s: отменяю — пока ждал очереди, повод отпал" % what)
        return False
    return True


def find_item(gsi, name):
    d, _ = gsi.snapshot()
    for slot, v in (d.get("items") or {}).items():
        if isinstance(v, dict) and v.get("name") == name:
            return slot, v
    return None, None


def track_bkb(gsi, st):
    if not BKB.get("enabled", True):
        return
    slot, item = find_item(gsi, BKB.get("item", "item_black_king_bar"))
    clock = gsi.get("map.clock_time", 0) or 0
    cd = (item or {}).get("cooldown", 0) or 0

    if item is not None:
        if cd > 0 and not st.get("bkb_cd"):
            st["bkb_cd"] = True
            st["bkb_pressed_at"] = clock
            st["bkb_uses"] = st.get("bkb_uses", 0) + 1
            log("BKB нажат (%d-й раз), откат %d с, герой hp %s%%"
                % (st["bkb_uses"], cd, gsi.get("hero.health_percent", "?")))
        elif cd == 0 and st.get("bkb_cd"):
            st["bkb_cd"] = False
            log("BKB готов снова")

    imm = bool(gsi.get("hero.magicimmune", False))
    if imm and not st.get("bkb_imm"):
        st["bkb_imm"] = True
        st["bkb_imm_at"] = clock
    elif not imm and st.get("bkb_imm"):
        st["bkb_imm"] = False
        held = clock - st.get("bkb_imm_at", clock)
        near = abs(st.get("bkb_imm_at", -999) - st.get("bkb_pressed_at", -999))
        if near <= BKB.get("link_s", 3):
            log("BKB кончился, держался %d с" % held)
        else:
            log("неуязвимость кончилась, держалась %d с (не от BKB)" % held)


def track_satanic(gsi, st):
    cfg_s = REACT.get("satanic", {})
    if not on("satanic"):
        return
    slot, item = find_item(gsi, cfg_s.get("item", "item_satanic"))
    if item is None or (item.get("cooldown") or 0) > 0:
        return
    hp = gsi.get("hero.health_percent", 100)
    if hp is None or hp > cfg_s.get("hp_percent", 40):
        return
    if not gsi.get("hero.alive", True):
        return
    def still():
        it = find_item(gsi, cfg_s.get("item", "item_satanic"))[1] or {}
        now = gsi.get("hero.health_percent", 100)
        return ((it.get("cooldown") or 0) == 0 and now is not None
                and now <= cfg_s.get("hp_percent", 40)
                and gsi.get("hero.alive", True))

    if not acquire_if(still, "сатаник"):
        return
    try:
        core.chord(*cfg_s.get("key", ["c"]))
    finally:
        core.INPUT_LOCK.release()
    st["satanic"] = st.get("satanic", 0) + 1
    log("САТАНИК: нажал на %s%% здоровья" % hp)


def track_death(gsi, st):
    cfg_x = REACT.get("death", {})
    if not on("death"):
        return
    alive = bool(gsi.get("hero.alive", True))
    was = st.get("was_alive", True)
    st["was_alive"] = alive
    if alive or not was:
        return
    if not acquire_if(lambda: not gsi.get("hero.alive", True), "смерть"):
        return
    st["deaths"] = st.get("deaths", 0) + 1
    try:
        core.chord(*cfg_x.get("key", ["f1"]))
    finally:
        core.INPUT_LOCK.release()
    log("герой умер — нажал %s" % "+".join(cfg_x.get("key", ["f1"])))


def track_disarm(gsi, st):
    cfg_d = REACT.get("disarm", {})
    if not on("disarm"):
        return
    now = time.time()
    if not gsi.get("hero.disarmed", False):
        st["disarm_since"] = None
        st["disarm_done"] = False
        return
    if st.get("disarm_since") is None:
        st["disarm_since"] = now
        return
    held = now - st["disarm_since"]
    if st.get("disarm_done") or held < cfg_d.get("after_s", 6):
        return
    if not acquire_if(lambda: gsi.get("hero.disarmed", False), "бессилие"):
        return
    try:
        core.chord(*cfg_d.get("key", ["e"]))
    finally:
        core.INPUT_LOCK.release()
    st["disarm_done"] = True
    log("БЕССИЛИЕ держится %.0f с — нажал %s"
        % (held, "+".join(cfg_d.get("key", ["e"]))))



_ANCHOR = {"at": 0.0, "x": None}


def hud_anchor():
    hunt = T.get("hunt") or {}
    if not hunt.get("strip"):
        return None
    if time.time() - _ANCHOR["at"] < hunt.get("cache_s", 5):
        return _ANCHOR["x"]
    found = core.find_patch(core.template(hunt.get("template", "hud_drop.png"),
                                          hunt.get("scale", 1.0)),
                            hunt["strip"])
    _ANCHOR["at"] = time.time()
    _ANCHOR["x"] = (found[0] if found and found[2] <= hunt.get("max_diff", 20)
                    else None)
    return _ANCHOR["x"]


def hud_shift(right=False):
    a = hud_anchor()
    hunt = T.get("hunt") or {}
    fixed = CO.get("talent_panel_toggle")
    if a is None or not fixed or not hunt.get("offset"):
        return 0
    base = fixed[0] - hunt["offset"][0]
    d = a - base
    return -d if right else d


def slot_xy(n):
    p = CO.get("inv_slot%d" % n)
    if not p:
        return p
    d = hud_shift(right=True)
    return [p[0] + d, p[1]] if d else p


def slot_of(gsi, name, backpack=None):
    d, _ = gsi.snapshot()
    for key, v in (d.get("items") or {}).items():
        m = re.fullmatch(r"slot([0-8])", key)
        if not m or not isinstance(v, dict) or v.get("name") != name:
            continue
        n = int(m.group(1)) + 1
        if backpack is True and n <= 6:
            continue
        if backpack is False and n > 6:
            continue
        return n
    return None


def inv_map(gsi):
    short = lambda x: (x or "-").replace("item_", "").replace("_custom", "")[:14]
    return " ".join("%d:%s" % (i, short(slot_item(gsi, i))) for i in range(1, 10))


def slot_item(gsi, n):
    d, _ = gsi.snapshot()
    name = ((d.get("items") or {}).get("slot%d" % (n - 1)) or {}).get("name")
    return None if name in (None, "empty") else name


def swap_slots(gsi, a, b, tries=3):
    pa, pb = slot_xy(a), slot_xy(b)
    if not pa or not pb:
        log("нет координат слотов %d/%d" % (a, b))
        return False

    want_b, want_a = slot_item(gsi, a), slot_item(gsi, b)
    if want_a is None and want_b is None:
        return True

    for i in range(1, tries + 1):
        core.drag(pa[0], pa[1], pb[0], pb[1])
        deadline = time.time() + LATE.get("swap_verify_s", 2.0)
        while time.time() < deadline:
            time.sleep(0.2)
            if slot_item(gsi, a) == want_a and slot_item(gsi, b) == want_b:
                return True
        log("  обмен слотов %d<->%d не подтвердился (попытка %d из %d)"
            % (a, b, i, tries))
    log("обмен слотов %d<->%d не удался" % (a, b))
    return False


def use_from_slot(gsi, name, target, key, verify, tries=6, backpack=None,
                  still=None):
    src = slot_of(gsi, name, backpack)
    if src is None:
        log("%s не найден в инвентаре: %s" % (name, inv_map(gsi)))
        return False

    if not acquire_if(still, name):
        return False
    try:
        moved = src != target
        if moved and not swap_slots(gsi, src, target):
            log("%s: не удалось переложить в слот %d" % (name, target))
            return False
        time.sleep(0.35)

        here = slot_of(gsi, name, backpack)
        if here != target:
            log("  %s: ждали в слоте %d, а он в %s — %s"
                % (name, target, here, inv_map(gsi)))
        ok = False
        for i in range(1, tries + 1):
            core.chord(*key)
            time.sleep(LATE.get("press_wait_s", 0.6))
            if verify():
                ok = True
                break
            if i == 1:
                log("  %s: нажатие не сработало, слот %s, %s"
                    % (name, slot_of(gsi, name, backpack), inv_map(gsi)))

        if moved:
            alive = slot_of(gsi, name, backpack) is not None
            back = swap_slots(gsi, target, src) if alive else swap_slots(gsi, src, target)
            if not back:
                log("%s: не смог вернуть слоты на место" % name)
    finally:
        core.INPUT_LOCK.release()
    return ok


def use_bkb(gsi, st):
    name = LATE.get("bkb_item", "item_black_king_bar")
    item = find_item(gsi, name)[1]
    if item is None:
        return False
    if (item.get("cooldown") or 0) > 0:
        return False

    def pressed():
        it = find_item(gsi, name)[1] or {}
        return (it.get("cooldown") or 0) > 0 or bool(gsi.get("hero.magicimmune", False))

    wave_now = st.get("wave")
    started = st.get("wave_at_real", 0)

    def still():
        return (st.get("wave") == wave_now and not st.get("wave_closed")
                and time.time() - started <= LATE.get("bkb_max_late_s", 8))

    ok = use_from_slot(gsi, name, LATE.get("swap_slot", 4),
                       LATE.get("bkb_key", ["v"]), pressed,
                       tries=LATE.get("bkb_press_tries", 1), still=still)
    log("BKB на волне %d: %s" % (st.get("wave", 0), "нажат" if ok else "НЕ нажат"))
    return ok


def use_moon(gsi, entry):
    name = entry["name"]

    def eaten():
        return find_item(gsi, name)[1] is None

    ok = use_from_slot(gsi, name, LATE.get("swap_slot", 4),
                       LATE.get("moon_key", ["alt", "v"]), eaten)
    log("%s: %s" % (entry["search"], "применён" if ok else "НЕ применён"))
    return ok


def in_stash(gsi, name):
    d, _ = gsi.snapshot()
    return any((v or {}).get("name") == name
               for k, v in (d.get("items") or {}).items()
               if k.startswith("stash"))


def boss_src(gsi, name, target):
    src = slot_of(gsi, name, backpack=True)
    if src is not None:
        return src, None
    have = count_item(gsi, name)
    if not have:
        return None, "нет в инвентаре"
    if in_stash(gsi, name) and slot_of(gsi, name) is None:
        return None, "лежит в стэше — курьер за ним не поедет"
    if have > 1:
        return None, "таких предметов %d, а в рюкзаке ни одного" % have
    return slot_of(gsi, name), None


def boss_daedalus(gsi, st, move_in):
    if not on("boss_swap"):
        return
    plan = LATE.get("boss_items") or [{"name": LATE.get("boss_item",
                                                        "item_greater_crit"),
                                       "slot": LATE.get("boss_slot", 6)}]
    if move_in:
        if not st.get("late_done"):
            log("БОСС: не перекладываю — докуп ещё не закончен")
            return
        wave_now = st.get("wave")
        if not acquire_if(lambda: st.get("wave") == wave_now
                          and not st.get("wave_closed"), "предметы на боссе"):
            return
        try:
            for e in plan:
                name, target = e["name"], e["slot"]
                src, why = boss_src(gsi, name, target)
                if src is None:
                    log("БОСС: %s не перекладываю — %s   %s"
                        % (name, why, inv_map(gsi)))
                    continue
                if src == target:
                    continue
                if swap_slots(gsi, src, target):
                    st.setdefault("boss_swap", []).append((target, src))
                    log("БОСС: %s из слота %d в слот %d" % (name, src, target))
        finally:
            core.INPUT_LOCK.release()
    elif st.get("boss_swap"):
        pairs = st.pop("boss_swap")
        with core.INPUT_LOCK:
            for a, b in reversed(pairs):
                swap_slots(gsi, a, b)
                log("БОСС: вернул слот %d на место" % b)


def maybe_boss_swap(gsi, st):
    if not st.get("boss_pending"):
        return
    name = LATE.get("bkb_item", "item_black_king_bar")
    if (on("bkb") and not st.get("wave_bkb")
            and find_item(gsi, name)[1] is not None):
        return
    st.pop("boss_pending", None)
    boss_daedalus(gsi, st, True)


def maybe_bkb(gsi, st):
    if not on("bkb") or st.get("wave_bkb"):
        return
    if st.get("wave_closed", True):
        return
    name = LATE.get("bkb_item", "item_black_king_bar")
    item = find_item(gsi, name)[1]
    if item is None or not st.get("wave_at_real"):
        return

    kind = st.get("wave_kind")
    delay = LATE.get("wave_delay_s", 2)
    for key, val in (LATE.get("wave_delay_by_unit") or {}).items():
        if kind and (key == kind or key in kind):
            delay = val
            break
    waited = time.time() - st["wave_at_real"]
    if waited < delay:
        return

    st["wave_bkb"] = True
    if (item.get("cooldown") or 0) > 0:
        st["bkb_skipped"] = st.get("bkb_skipped", 0) + 1
        log("BKB на волне %d: в откате (%d с), пропускаю волну"
            % (st.get("wave", 0), item.get("cooldown")))
        if st.pop("boss_pending", False):
            boss_daedalus(gsi, st, True)
        return

    if delay != LATE.get("wave_delay_s", 2):
        log("BKB: для %s жду %g с вместо обычных %g"
            % (creep_name(kind), delay, LATE.get("wave_delay_s", 2)))
    use_bkb(gsi, st)
    if st.pop("boss_pending", False):
        boss_daedalus(gsi, st, True)


def cast_name(which):
    return "ульта" if which == "ult" else which.replace("slot", "способность ")


def cast_key(which="ult"):
    mods = ("ctrl", "shift", "alt")
    keys = (AB.get("keys") or {}).get(which) or ["f"]
    return [k for k in keys if k not in mods] or ["f"]


def refresher(gsi, names):
    for name in names:
        item = find_item(gsi, name)[1]
        if item is not None:
            return name, item
    return None, None


def point(where):
    if not where:
        return None
    if where == "center":
        return core.SCREEN_W // 2, core.SCREEN_H // 2
    return int(where[0]), int(where[1])


def wave_f1(st, by=""):
    r = REACT.get("wave_start") or {}
    key = r.get("key")
    if not key or not on("wave_start"):
        return
    now = time.time()
    if now - st.get("wave_f1_at", 0) < float(r.get("min_gap_s", 3)):
        return
    if not acquire_if(None, "F1 в начале волны",
                      float(r.get("lock_wait_s", 1.0))):
        return
    times = max(1, int(r.get("times", 1) or 1))
    gap = float(r.get("gap_s", 0.08))
    try:
        st["wave_f1_at"] = now
        for i in range(times):
            if i:
                time.sleep(gap)
            core.chord(*key)
        st["wave_f1"] = st.get("wave_f1", 0) + 1
        log("волна %s: %s%s первым делом%s"
            % (st.get("wave", "?"), "+".join(key),
               (" ×%d" % times) if times > 1 else "",
               (" (по %s)" % by) if by else ""))
    finally:
        core.INPUT_LOCK.release()


BUTTON_NAME = {"left": "ЛКМ", "right": "ПКМ"}
BOSS_NAME = {"boss_attack": "связка"}


def maybe_boss_cast(gsi, st, what="boss_attack"):
    r = REACT.get(what) or {}
    if not r or not on(what):
        return
    mark = what + "_wave"
    if not st.get("wave_is_boss") or st.get(mark) == st.get("wave"):
        return
    if time.time() - st.get("wave_at_real", 0) < float(r.get("after_s", 0)):
        return
    if not acquire_if(lambda: st.get("wave_is_boss"), "%s на боссе" % what,
                      float(r.get("lock_wait_s", 1.5))):
        return
    try:
        st[mark] = st.get("wave")
        key = r.get("key")
        if key:
            core.chord(*key)
            time.sleep(float(r.get("key_wait_s", 0.1)))
        btn = (r.get("button") or "left").lower()
        at = point(r.get("click") or "center")
        if at:
            core.click(at[0], at[1], button=btn)
        bits = []
        if key:
            bits.append("+".join(key))
        if at:
            bits.append("%s в %d,%d"
                        % (BUTTON_NAME.get(btn, btn), at[0], at[1]))
        log("босс, %s: %s"
            % (BOSS_NAME.get(what, what), " и ".join(bits) or "нечего"))
    finally:
        core.INPUT_LOCK.release()


def maybe_wave_move(gsi, st):
    cfg_m = REACT.get("wave_move") or {}
    if not cfg_m or not on("wave_move"):
        return
    if st.get("wave_closed", True) or not st.get("wave_at_real"):
        return
    if st.get("wave_move_done") and st["wave_move_done"] == st.get("wave"):
        return
    units = cfg_m.get("unit") or []
    if isinstance(units, str):
        units = [units]
    if st.get("wave_kind") not in units:
        return

    need = cfg_m.get("prev") or []
    if need:
        hist = st.get("wave_hist") or []
        tail = hist[-len(need) - 1:-1]
        ok = len(tail) == len(need) and all(
            got in ([want] if isinstance(want, str) else want)
            for got, want in zip(tail, need))
        if not ok:
            st["wave_move_done"] = st.get("wave")
            log("связка на волне %d (%s): это другая арена — перед ней шли "
                "%s, а в крепости идут %s. Пропускаю."
                % (st.get("wave", 0), creep_name(st.get("wave_kind") or ""),
                   " -> ".join(creep_name(u) for u in tail) or "нет данных",
                   " -> ".join(creep_name(w[0] if not isinstance(w, str) else w)
                               for w in need)))
            return

    go, aim = point(cfg_m.get("move")), point(cfg_m.get("cast"))
    if not go and not aim:
        st["wave_move_done"] = st.get("wave")
        log("связка на волне %d (%s): точки не заданы — заполни в билде "
            "reactions.wave_move: move (куда ПКМ) и cast (куда ЛКМ)"
            % (st.get("wave", 0), creep_name(st.get("wave_kind") or "")))
        return

    st["wave_move_done"] = st.get("wave")
    wave_now = st.get("wave")
    if not acquire_if(lambda: st.get("wave") == wave_now
                      and not st.get("wave_closed"), "связка на волне",
                      timeout=cfg_m.get("lock_wait_s", 2.0)):
        st["wave_move_done"] = None
        return
    try:
        left = cfg_m.get("after_s", 0) - (time.time() - st["wave_at_real"])
        if left > 0:
            time.sleep(left)
        done = []
        if go:
            core.click(go[0], go[1], button="right")
            done.append("ПКМ в %d,%d" % go)
            time.sleep(cfg_m.get("move_wait_s", 1.5))
        key = cfg_m.get("key")
        if key:
            core.chord(*key)
            done.append("+".join(key))
            time.sleep(cfg_m.get("key_wait_s", 0.1))
        if aim:
            core.click(aim[0], aim[1])
            done.append("ЛКМ в %d,%d" % aim)
        log("связка на волне %d (%s): %s"
            % (st.get("wave", 0), creep_name(st.get("wave_kind") or ""),
               ", ".join(done)))
    finally:
        core.INPUT_LOCK.release()


def after_cast(st):
    a = (REACT.get("ult") or {}).get("after_cast") or {}
    if not a:
        return
    key = a.get("key")
    if key:
        core.chord(*key)
        time.sleep(a.get("key_wait_s", 0.1))
    where = a.get("click")
    if not where:
        return
    if where == "center":
        x, y = core.SCREEN_W // 2, core.SCREEN_H // 2
    else:
        x, y = where
    core.click(x, y)
    st["after_cast"] = st.get("after_cast", 0) + 1


def maybe_refresh(gsi, st, which, key):
    r = ((REACT.get("ult") or {}).get("refresh")) or {}
    if not r or not on("refresh"):
        return False
    names = r.get("item") or ["item_refresher_custom", "item_refresher"]
    if isinstance(names, str):
        names = [names]
    name, item = refresher(gsi, names)
    if item is None:
        return False
    cd = item.get("cooldown") or 0
    if cd > 0:
        log("рефрешер на волне %d: сам в откате (%d с)"
            % (st.get("wave", 0), cd))
        return False

    rkey = r.get("key") or ["v"]
    gap = r.get("gap_s", 0)
    if gap:
        time.sleep(gap)
    if r.get("slot"):
        use_from_slot(gsi, name, r["slot"], rkey, lambda: True, tries=1)
    else:
        core.chord(*rkey)
    st["refresh_uses"] = st.get("refresh_uses", 0) + 1

    wait = r.get("repeat_after_s", 0.5)
    if wait:
        time.sleep(wait)
    core.chord(*key)
    st["ult_at"] = time.time()
    st["ult_uses"] = st.get("ult_uses", 0) + 1
    after_cast(st)
    log("рефрешер на волне %d: нажал %s, следом %s второй раз (%s)"
        % (st.get("wave", 0), "+".join(rkey), cast_name(which),
           "+".join(key)))
    return True


def maybe_ult(gsi, st):
    cfg_u = REACT.get("ult") or {}
    if not cfg_u or not on("ult") or st.get("wave_ult"):
        return
    if st.get("wave_closed", True) or not st.get("wave_at_real"):
        return
    if st.get("wave_is_boss") and not cfg_u.get("on_boss", True):
        st["wave_ult"] = True
        return

    last = st.get("ult_at", 0)
    if last and last >= st["wave_at_real"] - cfg_u.get("own_grace_s", 2.0):
        st["wave_ult"] = True
        return

    left = cfg_u.get("after_s", 1) - (time.time() - st["wave_at_real"])
    if left > WAVE.get("poll_s", 1.0):
        return

    which = cfg_u.get("ability", "ult")
    i = ability_slots(gsi).get(which)
    if i is None:
        return
    ab = gsi.get("abilities.ability%d" % i) or {}
    if not ab.get("level"):
        return
    cd = ab.get("cooldown") or 0
    if cd > 0 or not ab.get("can_cast", True):
        st["wave_ult"] = True
        st["ult_skipped"] = st.get("ult_skipped", 0) + 1
        log("ульта на волне %d: %s, пропускаю волну"
            % (st.get("wave", 0),
               ("в откате (%d с)" % cd) if cd else "нельзя применить"))
        return

    if left > 0:
        time.sleep(left)

    wave_now, started = st.get("wave"), st["wave_at_real"]

    def still():
        return (st.get("wave") == wave_now and not st.get("wave_closed")
                and time.time() - started <= cfg_u.get("max_late_s", 8))

    do_cast(gsi, st, still=still, by="GSI")


def do_cast(gsi, st, still=None, by=""):
    cfg_u = REACT.get("ult") or {}
    gap = cfg_u.get("min_gap_s", 3)
    if gap and time.time() - st.get("ult_at", 0) < gap:
        return
    which = cfg_u.get("ability", "ult")
    i = ability_slots(gsi).get(which)
    if i is None:
        return
    key = cfg_u.get("key") or cast_key(which)
    if not acquire_if(still, which, timeout=cfg_u.get("lock_wait_s", 1.0)):
        return
    try:
        st["wave_ult"] = True
        st["ult_at"] = time.time()
        core.chord(*key)
        st["ult_uses"] = st.get("ult_uses", 0) + 1
        log("%s на волне %d: нажал %s%s"
            % (cast_name(which), st.get("wave", 0), "+".join(key),
               " (по %s)" % by if by else ""))
        if not maybe_refresh(gsi, st, which, key):
            after_cast(st)
    finally:
        core.INPUT_LOCK.release()


def close_wave(st, clock, why):
    if not st.get("wave") or st.get("wave_closed"):
        return
    st["wave_closed"] = True
    if st.get("boss_swap"):
        boss_daedalus(st["gsi"], st, False)
    held = time.time() - st.get("wave_at_real", time.time())
    log("ВОЛНА %d: %s за %d с%s"
        % (st["wave"], why, round(held),
           "  (босс)" if st.get("wave_is_boss") else ""))


WAVE_UNIT = re.compile(r"^npc_woda_creep\d+")


def is_boss(unitname):
    if "boss_" not in unitname:
        return False
    return not any(x in unitname for x in WAVE.get("not_boss", []))


def starts_wave(unitname):
    return bool(WAVE_UNIT.match(unitname)) or is_boss(unitname)


def since_wave(st, clock):
    if clock and st.get("wave_at"):
        return clock - st["wave_at"]
    return time.time() - st.get("wave_at_real", 0)


def track_waves(gsi, st):
    if not WAVE.get("enabled", True):
        return
    en = enemy_creeps(gsi)
    clock = gsi.get("map.clock_time", 0) or 0
    counts = collections.Counter(v["unitname"] for v in en
                                 if starts_wave(v["unitname"]))
    prev = st.get("prev_counts", {})
    st["prev_counts"] = dict(counts)
    n = sum(counts.values())

    bosses = [u for u in counts if is_boss(u) and counts[u] > prev.get(u, 0)]
    jumps = {u: counts[u] - prev.get(u, 0) for u in counts if not is_boss(u)}
    big = [u for u, d in jumps.items() if d >= WAVE.get("spawn_count", 3)]

    kind = None
    if bosses:
        kind = bosses[0]
    elif big:
        if since_wave(st, clock) >= WAVE.get("min_gap_s", 5):
            kind = max(big, key=lambda u: jumps[u])

    if kind:
        close_wave(st, clock, "сменилась")
        boss = is_boss(kind)
        st.update(wave_closed=False, wave_low=False, wave_at=clock,
                  wave_at_real=time.time(), wave_kind=kind, wave_bkb=False,
                  wave_ult=False, wave_is_boss=boss)
        st["wave"] = st.get("wave", 0) + 1
        st["wave_hist"] = (st.get("wave_hist") or [])[-5:] + [kind]
        log("ВОЛНА %d: %s%s +%d, время %d:%02d, герой ур.%d hp %s%%"
            % (st["wave"], creep_name(kind), "  <-- БОСС" if boss else "",
               jumps.get(kind, counts.get(kind, 1)), clock // 60, clock % 60,
               gsi.get("hero.level", 0) or 0, gsi.get("hero.health_percent", "?")))
        if boss:
            st["bosses"] = st.get("bosses", 0) + 1
            st["boss_pending"] = True
        return

    alive = counts.get(st.get("wave_kind"), 0)
    if alive == 0 and n <= WAVE.get("clear_count", 6) and not st.get("wave_low", True):
        st["wave_low"] = True
        close_wave(st, clock, "зачищена")


def right_hero(gsi, st):
    want = cfg.get("gsi_name")
    got = gsi.get("hero.name", "") or ""
    if not want or not got or got == want:
        return True
    if not st.get("wrong_hero"):
        st["wrong_hero"] = got
        log("НЕ ТОТ ГЕРОЙ: в игре %s, а план написан под %s. Таланты не "
            "трогаю — по чужому дереву они возьмутся наугад." % (got, want))
        core.save_shot("wrong_hero.jpg")
    return False


def cell_box(tree, row, col):
    x, y = cell_xy(tree, row, col)
    left, up, right, down = T.get("cell_pad", [46, 46, 46, 58])
    return [int(x - left), int(y - up), int(x + right), int(y + down)]


def verify_talent(before, box):
    thr = T.get("cell_change", 1.2)
    deadline = time.time() + T["verify_timeout_ms"] / 1000.0
    while time.time() < deadline:
        time.sleep(0.09)
        if core.patch_diff(before, core.patch(box)) >= thr:
            time.sleep(T.get("cell_confirm_s", 0.15))
            if core.patch_diff(before, core.patch(box)) >= thr:
                return True
    return False


_GRID = None


def grid_rules():
    global _GRID
    if _GRID is None:
        _GRID = load_grid() or {}
    return _GRID


def why_stuck(done):
    grid = grid_rules()
    if not grid or done >= len(STEPS):
        return ""
    cells, need = grid["cells"], grid["row_need"]
    tree, row, col, rank, of = STEPS[done]
    cell = cells.get("%d,%d,%d" % (tree, row, col))
    if not cell:
        return ""
    mine = sum(1 for s in STEPS[:done] if s[0] == tree)
    out = ("  (%s ряд %d: открывается с %d в дереве, по моему счёту %d"
           % (TREE_NAME[tree], row, need[row - 1], mine))
    if cell.get("req"):
        out += ", и нужен «%s» ровно %d ранга" % (cell["req"][0], cell["req"][1])
    return out + ")"


def bkb_pending(gsi, st):
    if not on("bkb") or st.get("wave_bkb"):
        return False
    if st.get("wave_closed", True) or not st.get("wave_at_real"):
        return False
    if find_item(gsi, LATE.get("bkb_item", "item_black_king_bar"))[1] is None:
        return False
    return time.time() - st["wave_at_real"] <= LATE.get("bkb_max_late_s", 8)


def spend_talents(gsi, st):
    if not on("talents"):
        return
    done = st["done"]
    allowed = allowed_steps(gsi)
    level0 = gsi.get("hero.level", 0) or 0

    if done >= allowed:
        if done < len(STEPS) and not st.get("phase_noted"):
            st["phase_noted"] = True
            log("первые %d талантов взяты — остальные %d после %d-й минуты"
                % (allowed, len(STEPS) - allowed, T.get("phase2_after_s", 600) // 60))
        return

    level = gsi.get("hero.level", 0) or 0

    f = st.get("fail")
    if f and f["step"] == done and level <= f["level"]:
        if level < T.get("max_level", 30):
            return
        if time.time() - f.get("at", 0) < T.get("maxlevel_retry_s", 180):
            return
        f["at"] = time.time()
        log("максимальный уровень — пробую шаг %d заново" % (done + 1))

    if not gsi.get("hero.alive", True):
        return

    if bkb_pending(gsi, st):
        return

    core.INPUT_LOCK.acquire()
    toggle_panel(True)
    log("панель открыта: шаг %d из %d (сейчас доступно %d), взято %d, уровень %d"
        % (done + 1, len(STEPS), allowed, st["learned"], level))

    try:
        while done < allowed:
            guard()
            tree, row, col, rank, of = STEPS[done]
            x, y = cell_xy(tree, row, col)
            box = cell_box(tree, row, col)
            core.glide(x, y)
            time.sleep(T.get("hover_settle_s", 0.12))
            before = core.patch(box)
            core.click(x, y)

            if verify_talent(before, box):
                done += 1
                st["done"] = done
                st["learned"] += 1
                st["fail"] = None
                log("талант %2d/%d: %s строка %d кол %d  (ранг %d из %d)"
                    % (done, len(STEPS), TREE_NAME[tree], row, col, rank, of))
                time.sleep(T["click_delay_ms"] / 1000.0)
                continue

            cur = gsi.get("hero.level", 0) or 0
            st["fail"] = {"step": done, "level": cur, "at": time.time()}
            if st.get("shot_step") != done:
                st["shot_step"] = done
                p = core.save_shot("stuck_%02d.jpg" % (done + 1))
                if p:
                    log("снимок застрявшего шага: %s" % os.path.basename(p))
            if cur >= T.get("max_level", 30):
                log("шаг %d не прошёл — закрываю панель, повтор через %d с%s"
                    % (done + 1, T.get("maxlevel_retry_s", 180), why_stuck(done)))
            else:
                log("шаг %d не взялся — очков больше нет, жду новый уровень"
                    " (взято %d из %d, уровень %d)%s"
                    % (done + 1, done, len(STEPS),
                       cur, why_stuck(done)))
            break
    finally:
        toggle_panel(False)
        core.INPUT_LOCK.release()



def ability_slots(gsi):
    d, _ = gsi.snapshot()
    found = []
    for k, v in (d.get("abilities") or {}).items():
        m = re.fullmatch(r"ability(\d+)", k)
        if m and isinstance(v, dict):
            found.append((int(m.group(1)), v))
    found.sort()
    slots = {}
    plain = [i for i, v in found if not v.get("ultimate")]
    for n, i in enumerate(plain, 1):
        slots["slot%d" % n] = i
    ults = [i for i, v in found if v.get("ultimate")]
    if ults:
        slots["ult"] = ults[0]
    return slots


def ability_level(gsi, i):
    return gsi.get("abilities.ability%d.level" % i, 0) or 0


def wait_ability(gsi, i, before, timeout_ms):
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        if ability_level(gsi, i) > before:
            return True
        time.sleep(0.12)
    return False


def abilities_done(gsi, st):
    if st.get("abilities_done"):
        return True
    caps = AB.get("max_level") or {}
    slots = ability_slots(gsi)
    if not caps or not slots:
        return False
    for name in AB["priority"]:
        i, cap = slots.get(name), caps.get(name)
        if i is None or not cap or ability_level(gsi, i) < cap:
            return False
    st["abilities_done"] = True
    log("способности в максимуме (%s) — больше не трогаю"
        % ", ".join("%s %d" % (n, caps[n]) for n in AB["priority"]))
    after_abilities(st)
    return True


def after_abilities(st):
    key = (AB.get("after_max") or {}).get("key")
    if not key or st.get("after_max_done"):
        return
    st["after_max_done"] = True
    with core.INPUT_LOCK:
        core.chord(*key)
    log("способности готовы — нажал %s" % "+".join(key))


def ult_ready(level, have, st=None):
    steps = AB.get("ult_levels") or [6, 12, 18]
    if have >= len(steps):
        return True
    need = steps[have]
    if level >= need:
        return True
    if st is not None and st.get("ult_wait") != need:
        st["ult_wait"] = need
        log("ульта: следующий ранг с %d уровня, пока качаю обычные" % need)
    return False


def level_abilities(gsi, st):
    if not on("abilities") or abilities_done(gsi, st):
        return False

    level = gsi.get("hero.level", 0) or 0
    if level <= st["ability_level"] and not st.get("ability_retry"):
        return False

    slots = ability_slots(gsi)
    if not slots:
        return False
    maxed = st.setdefault("maxed", {})
    caps = AB.get("max_level") or {}

    ac = AB["autocast"]
    i1 = slots.get(ac["slot"])
    if not st["autocast"] and i1 is not None and ability_level(gsi, i1) >= 1:
        core.chord(*ac["combo"])
        st["autocast"] = True
        log("первый скилл на автокаст (%s)" % "+".join(ac["combo"]))

    tried = []
    for name in AB["priority"]:
        i = slots.get(name)
        if i is None or maxed.get(name, -1) >= level:
            continue
        cap = caps.get(name)
        if cap and ability_level(gsi, i) >= cap:
            continue
        if name == "ult" and not ult_ready(level, ability_level(gsi, i),
                                           st):
            continue
        guard()
        before = ability_level(gsi, i)
        with core.INPUT_LOCK:
            core.chord(*AB["keys"][name])
        if wait_ability(gsi, i, before, AB["verify_timeout_ms"]):
            for dead in tried:
                maxed[dead] = level
                log("способность %s не качается на уровне %d — пропускаю "
                    "до следующего" % (dead, level))
            st["ability_retry"] = True
            log("способность %-5s (ability%d) -> уровень %d  (герой ур.%d)"
                % (name, i, ability_level(gsi, i), level))
            return True
        tried.append(name)

    st["ability_retry"] = False
    st["ability_level"] = level
    return False



def inventory(gsi):
    d, _ = gsi.snapshot()
    inv = {}
    for k, v in (d.get("items") or {}).items():
        if not k.startswith(("slot", "stash")):
            continue
        name = (v or {}).get("name")
        if name and name != "empty":
            inv[name] = inv.get(name, 0) + 1
    return inv


def count_item(gsi, name):
    return inventory(gsi).get(name, 0)


def clear_search():
    p = CO["shop_search_field"]
    core.click(*p)
    time.sleep(0.15)
    core.chord("ctrl", "a")
    scan, ext = core.resolve_key("backspace")
    for _ in range(28):
        core.tap(scan, hold=0.015, extended=ext)
    time.sleep(0.15)


def note_price(gsi, entry, gold_before):
    time.sleep(0.4)
    spent = gold_before - (gsi.get("player.gold", 0) or 0)
    core.learn_price(entry["name"], spent)


def buy(gsi, entry):
    need("shop_toggle")
    btn = ITEMS.get("result_button", "right")
    point = (ITEMS.get("shop_points") or {}).get(entry["name"])

    for attempt in range(1, ITEMS["max_retries"] + 1):
        guard()
        before = inventory(gsi)
        core.INPUT_LOCK.acquire()
        gold_before = gsi.get("player.gold", 0) or 0
        log("  попытка %d: открываю магазин %s" % (attempt, CO["shop_toggle"]))
        core.click(*CO["shop_toggle"])
        time.sleep(0.6)

        if point:
            log("  %s кнопкой по предмету %s в магазине %s"
                % ("правой" if btn == "right" else "левой", entry["search"], point))
            core.click(*point, button=btn)
        else:
            need("shop_search_field", "shop_first_result")
            log("  чищу поле поиска %s" % (CO["shop_search_field"],))
            clear_search()
            log("  печатаю «%s»" % entry["search"])
            core.type_text(entry["search"])
            time.sleep(0.8)
            log("  %s кнопкой по первому результату %s"
                % ("правой" if btn == "right" else "левой", CO["shop_first_result"]))
            core.click(*CO["shop_first_result"], button=btn)

        time.sleep(0.5)
        core.click(*CO["shop_toggle"])
        hold = bool(entry.get("stash"))
        if not hold:
            core.INPUT_LOCK.release()
        try:
            deadline = time.time() + ITEMS["verify_timeout_ms"] / 1000.0
            while time.time() < deadline:
                now = inventory(gsi)
                if now.get(entry["name"], 0) > before.get(entry["name"], 0):
                    note_price(gsi, entry, gold_before)
                    if hold:
                        to_stash(gsi, entry)
                    return "ok"

                spent = gold_before - (gsi.get("player.gold", 0) or 0)
                if spent >= min(entry["cost"] * 0.5, 1500):
                    log("  золото ушло: %d (цена %d) — покупка состоялась"
                        % (spent, entry["cost"]))
                    core.learn_price(entry["name"], spent)
                    if hold:
                        to_stash(gsi, entry)
                    return "ok"
                ours = {o["name"] for o in ITEMS["order"]} - {entry["name"]}
                extra = [k for k, v in now.items()
                         if v > before.get(k, 0) and k in ours]
                if extra:
                    log("  вместо %s купилось: %s"
                        % (entry["search"], ", ".join(extra)))
                    return "wrong"
                time.sleep(0.1 if hold else 0.2)
            log("  покупка %s не подтвердилась (попытка %d)"
                % (entry["search"], attempt))
        finally:
            if hold:
                core.INPUT_LOCK.release()
    return "none"


def stash_names(name):
    base = name[:-7] if name.endswith("_custom") else name
    out = [name, base, base + "_custom"]
    return list(dict.fromkeys(out))


def to_stash(gsi, entry):
    names = stash_names(entry["name"])
    btn = CO.get("stash_button")
    for attempt in range(1, ITEMS.get("stash_tries", 3) + 1):
        if any(in_stash(gsi, x) for x in names):
            return True
        src = next((s for s in (slot_of(gsi, x) for x in names)
                    if s is not None), None)
        if src is None:
            log("  %s в тайник не убираю — не нашёл его в инвентаре   %s"
                % (entry["search"], inv_map(gsi)))
            return False
        p = slot_xy(src)
        if not p:
            log("  %s в тайник не убираю — нет координаты слота %d"
                % (entry["search"], src))
            return False
        with core.INPUT_LOCK:
            log("  убираю %s в тайник: правая по слоту %d %s%s (попытка %d)"
                % (entry["search"], src, p,
                   ", затем кнопка %s" % (btn,) if btn
                   else ", кнопка не задана (coords.stash_button в профиле)",
                   attempt))
            core.click(p[0], p[1], button="right")
            if btn:
                time.sleep(ITEMS.get("stash_menu_wait_s", 0.35))
                core.click(btn[0], btn[1])
        deadline = time.time() + ITEMS.get("stash_timeout_ms", 1500) / 1000.0
        while time.time() < deadline:
            if any(in_stash(gsi, x) for x in names):
                log("  %s в тайнике, слот %d освободился"
                    % (entry["search"], src))
                return True
            time.sleep(0.15)
    log("  %s в тайник не ушёл за %d попыток   %s"
        % (entry["search"], ITEMS.get("stash_tries", 3), inv_map(gsi)))
    return False


def books_spend_all(gsi):
    btn = ITEMS.get("result_button", "right")
    floor = END["cost"] + END.get("keep_gold", 0)
    cap = END.get("max_per_visit", 6)
    n = 0
    while True:
        guard()
        gold = gsi.get("player.gold", 0) or 0
        if gold < floor:
            log("золота %d, меньше цены — докуп закончен (куплено %d)" % (gold, n))
            return n
        if cap and n >= cap:
            log("за этот заход хватит книг (%d), остальное в следующий раз" % n)
            return n
        core.click(*CO["shop_first_result"], button=btn)
        deadline = time.time() + 2.5
        while time.time() < deadline:
            now = gsi.get("player.gold", 0) or 0
            if gold - now >= END["cost"] * 0.5:
                n += 1
                log("  книга %d куплена, золота осталось %d" % (n, now))
                break
            time.sleep(0.15)
        else:
            log("книга не покупается — прекращаю докуп (куплено %d)" % n)
            return n
        time.sleep(END["buy_delay_ms"] / 1000.0)


def arm_books(gsi):
    need("shop_toggle", "shop_search_field", "shop_first_result", "quickbuy_slot")
    with core.INPUT_LOCK:
        log("открываю магазин под %s" % END["search"])
        core.click(*CO["shop_toggle"])
        time.sleep(0.6)
        clear_search()
        core.type_text(END["search"])
        time.sleep(0.8)

        books_spend_all(gsi)

        log("кладу %s в квикбай (shift + левая)" % END["search"])
        core.click(*CO["shop_first_result"], button="left", mods=("shift",))
        time.sleep(0.4)
        core.click(*CO["shop_toggle"])
        time.sleep(0.3)
        core.click(*CO["quickbuy_slot"], button="right")


def buy_late(gsi, st):
    if not on("late"):
        return True
    order = LATE.get("order", [])
    i = st.get("late", 0)
    while i < len(order):
        e = order[i]
        gold = gsi.get("player.gold", 0) or 0
        if gold < e["cost"]:
            st["late"] = i
            return False
        log("докуп %s (цена %d, золота %d)" % (e["search"], e["cost"], gold))
        if buy(gsi, e) != "ok":
            st["late"] = i
            return False
        i += 1
        st["late"] = i
        if e.get("role") == "use_once" and on("moon"):
            use_moon(gsi, e)
        time.sleep(0.6)
    return True


def farm_books(gsi, st):
    if not END.get("name") or not END.get("cost"):
        return False
    if not on("books") or not CO.get("quickbuy_slot"):
        return False

    if not st.get("books_armed"):
        guard()
        arm_books(gsi)
        st["books_armed"] = True
        st["rich_since"] = None
        log("книги взведены: %s лежит в квикбае, дальше игра покупает сама"
            % END["search"])
        return True

    gold = gsi.get("player.gold", 0) or 0
    limit = END["cost"] * 3 + END.get("keep_gold", 0)
    if gold >= limit:
        if st.get("rich_since") is None:
            st["rich_since"] = time.time()
        elif time.time() - st["rich_since"] > 30:
            log("золото копится (%d), автопокупка похоже слетела — взвожу заново"
                % gold)
            st["books_armed"] = False
            st["rich_since"] = None
            return True
    else:
        st["rich_since"] = None
    return False


def buy_items(gsi, bought, st):
    order = ITEMS["order"]
    if not on("items") or st.get("items_halted"):
        return bought
    while bought < len(order):
        e = order[bought]
        gold = gsi.get("player.gold", 0) or 0
        if gold < e["cost"]:
            return bought
        log("покупаю %s (цена %d, золота %d)" % (e["search"], e["cost"], gold))
        res = buy(gsi, e)
        if res == "ok":
            bought += 1
            log("куплено %d/%d: %s" % (bought, len(order), e["search"]))
            time.sleep(0.6)
            continue
        if res == "wrong":
            st["items_halted"] = True
            log("ОСТАНАВЛИВАЮ покупки: вместо %s купилось другое. "
                "Скорее всего не очистилось поле поиска." % e["search"])
            return bought
        log("не куплено: %s — вернусь на следующем круге" % e["search"])
        return bought
    return bought



def wait_for(pred, timeout, what):
    deadline = time.time() + timeout
    while time.time() < deadline:
        guard()
        if pred():
            return True
        time.sleep(0.5)
    log("не дождался: %s" % what)
    return False


def at_server_screen():
    m = next((m for _, _, _, _, _, m in PREGAME
              if m and m.get("point") == "server_pick"), None)
    p = CO.get("server_pick")
    if not m or not p:
        return False
    try:
        return core.color_frac(core._patch(p[0], p[1], m["w"], m["h"]),
                               m["color"]) >= m["min_frac"]
    except Exception:
        return False



PARTY = None
PRESS_SERVER = True
TOGETHER = False


def party_on():
    return PARTY is not None and PARTY.on


def party_role():
    return PARTY.role if party_on() else ""


def party_start():
    global PARTY
    conf = party.conf_of(cfg)
    if not conf.get("on"):
        return
    PARTY = party.Link(conf, hero=cfg.get("hero_file") or cfg.get("hero") or "")
    if not PARTY.start():
        PARTY = None


def party_stop():
    global PARTY
    if PARTY is not None:
        PARTY.stop()
        PARTY = None


def party_refresh():
    conf = party.conf_of(cfg)
    if bool(conf.get("on")) != party_on():
        if conf.get("on"):
            log("пати включена с пульта")
            party_start()
        else:
            log("пати выключена с пульта — играю соло")
            party_stop()
        return
    if party_on():
        now = (str(conf.get("role") or ""), int(conf.get("port") or 0),
               str(conf.get("token") or ""))
        if now != (PARTY.role, PARTY.port, PARTY.token):
            PARTY.once("restart", "пати: роль, порт или пароль изменились — "
                                  "применится после перезапуска бота")


def party_invite():
    conf = party.conf_of(cfg)
    miss = [k for k in ("party_field", "party_invite") if not CO.get(k)]
    if miss:
        log("пати: нет координат %s — сними их на пульте" % ", ".join(miss))
        return False
    who = str(conf.get("invite_id") or "").strip()
    if not who:
        log("пати: не задан invite_id — звать некого")
        return False
    pause = float(conf.get("field_wait_s") or 0.4)
    with core.INPUT_LOCK:
        core.click(*CO["party_field"])
        time.sleep(pause)
        core.chord("ctrl", "a")
        scan, ext = core.resolve_key("backspace")
        for _ in range(24):
            core.tap(scan, hold=0.015, extended=ext)
        core.type_text(who)
        time.sleep(pause)
        core.click(*CO["party_invite"])
    log("пати: позвал напарника (%s)" % who)
    return True


def party_accept():
    if not CO.get("party_accept"):
        log("пати: нет координаты party_accept — сними её на пульте")
        return False
    acc = party.conf_of(cfg).get("accept") or {}
    if acc.get("color"):
        if not core.wait_color(CO["party_accept"][0], CO["party_accept"][1],
                               color=acc.get("color"),
                               min_frac=float(acc.get("min_frac") or 0.12),
                               w=int(acc.get("w") or 120),
                               h=int(acc.get("h") or 44),
                               timeout=float(acc.get("timeout_s") or 60)):
            log("пати: окно приглашения так и не появилось")
            return False
    else:
        time.sleep(1.5)
    with core.INPUT_LOCK:
        core.click(*CO["party_accept"])
    log("пати: принял приглашение")
    return True


def lobby_mode_want():
    if party_on() and TOGETHER:
        return (party.conf_of(cfg).get("size") or "duo").lower()
    return "solo"


def set_lobby_mode(want=None):
    want = (want or lobby_mode_want()).lower()
    row = (cfg.get("lobby_modes") or {}).get(want) or {}
    at, mark = row.get("at"), row.get("mark")
    if not at:
        return
    if mark:
        try:
            if core.mean_bright(mark[0], mark[1], 14) > 120:
                return
        except Exception:
            pass
    with core.INPUT_LOCK:
        core.click(*at)
    log("режим лобби: %s" % (row.get("name") or want.upper()))
    time.sleep(0.4)


def party_ready():
    if not party_on() or PARTY.alive():
        return
    conf = party.conf_of(cfg)
    if not wait_for(PARTY.alive, float(conf.get("wait_s") or 300),
                    "напарника на связи"):
        log("пати: напарника не слышно — захожу один")


def party_sync():
    global PRESS_SERVER, TOGETHER
    PRESS_SERVER, TOGETHER = True, False
    if not party_on():
        return False
    conf = party.conf_of(cfg)
    ready = at_server_screen() or wait_for(
        at_server_screen, float(conf.get("lobby_wait_s") or 60),
        "зелёную кнопку в лобби")
    if not ready:
        log("пати: лобби так и не прорисовалось — в этот раз играю соло")
        return False
    set_lobby_mode(conf.get("size") or "duo")
    PARTY.hero = cfg.get("hero_file") or cfg.get("hero") or ""
    together, press = party.handshake(PARTY, conf, party_invite,
                                      party_accept, guard)
    PRESS_SERVER, TOGETHER = press, together
    return together


def party_lost(st):
    if not party_on() or not TOGETHER:
        return ""
    conf = party.conf_of(cfg)
    if not conf.get("rejoin_together", True):
        return ""
    gap = float(conf.get("lost_s") or 60)
    if not PARTY.alive():
        quiet = time.time() - (PARTY.peer_at or 0)
        if quiet > gap:
            return "напарника не слышно %d с" % round(quiet)
        return ""
    was = st.get("peer_cycle")
    if was is not None and PARTY.peer_cycle != was:
        return "напарник пошёл заходить заново — похоже, вылетел"
    return ""


def leave_match(why):
    log("выхожу из матча: %s" % why)
    with core.INPUT_LOCK:
        core.console_command("disconnect", cfg["launch"]["console_key"])
    time.sleep(3)


def party_step(pick, label):
    role = party_role()
    if role == "second" and TOGETHER:
        if pick == "difficulty_pick":
            log("%s: в пати это делает мейн — пропускаю" % label)
            return False
        if pick == "server_pick" and not PRESS_SERVER:
            log("%s: на этом матче жмёт мейн — пропускаю" % label)
            return False
        if pick == "hero_pick":
            conf = party.conf_of(cfg)
            PARTY.wait("hero", float(conf.get("hero_wait_s") or 60),
                       "мейна с выбором героя", guard)
    return True


def launch_game(gsi):
    if not core.focus_dota():
        log("окно Dota не найдено — поднимаю игру через Steam")
        if not machine.start_dota():
            log("Dota не поднялась — проверь Steam")
            return False
        nap(WATCH.get("after_restart_s", 60), "даю игре выйти в меню")
        if not core.focus_dota():
            log("окно Dota так и не появилось")
            return False
    if at_server_screen():
        log("лобби аркады уже открыто — работаю прямо с экрана")
        return True
    log("захожу в кастомку")
    core.console_command(cfg["launch"]["command"], cfg["launch"]["console_key"])
    return wait_for(lambda: gsi.state != "" or at_server_screen(),
                    120, "загрузка кастомки")


def difficulty_name():
    lst = cfg.get("difficulties") or []
    n = int(cfg.get("difficulty", 1) or 1)
    if 1 <= n <= len(lst):
        return "%d (%s)" % (n, lst[n - 1].get("name", "?"))
    return str(n)


def build_pregame():
    return [
        ("server_pick", "server_confirm", "ищу игру", 0.8, 3,
         dict(point="server_pick", color="green", min_frac=0.18,
              w=56, h=56)),
        ("difficulty_pick", "difficulty_confirm",
         "беру сложность %s" % difficulty_name(), 1.0, 3,
         dict(point="difficulty_confirm", color="gold", min_frac=0.10,
              w=240, h=56)),
        ("hero_pick", "hero_pick_confirm",
         "беру %s" % cfg.get("title", "героя"), 1.2, 2, None),
    ]


PREGAME = build_pregame()


def apply_config(new):
    global STEPS, PREGAME
    bind_config(new)
    STEPS = plan_steps()
    PREGAME = build_pregame()


def sweep_ready(name, screen):
    hero = core.read_json(core.hero_path(name))
    if not hero:
        return False, "билд не читается"
    at = hero.get("pick_at")
    at = at.get(screen) if isinstance(at, dict) else at
    if not at or not any(at):
        return False, "нет точки пика для %s" % screen
    return True, hero.get("gsi_name") or ""


def next_hero():
    diff = str(cfg.get("difficulty", ""))
    row = core.progress().get(diff) or {}
    screen = cfg.get("screen_file") or ""
    names = core.hero_files()
    if (cfg.get("sweep") or {}).get("order") == "random":
        names = sorted(names)
        random.Random(diff).shuffle(names)
    skipped = []
    queue = []
    for i, name in enumerate(names):
        ok, info = sweep_ready(name, screen)
        if not ok:
            skipped.append(name)
            continue
        rec = row.get(info) or {}
        if rec.get("passed"):
            continue
        queue.append((rec.get("tries", 0), i, name))
    if not queue:
        return None, skipped
    queue.sort()
    return queue[0][2], skipped


def sweep_pick():
    if (cfg.get("mode") or "solo") != "sweep":
        return None
    name, skipped = next_hero()
    diff = str(cfg.get("difficulty", ""))
    row = core.progress().get(diff) or {}
    ok = sum(1 for v in row.values() if v.get("passed"))
    if not name:
        if not globals().get("_sweep_said"):
            globals()["_sweep_said"] = True
            log("обход: сложность %s прошли все %d героев, кому есть чем "
                "пикаться. Дальше играю тем же билдом." % (diff, ok))
        return None
    globals()["_sweep_said"] = False
    if name != cfg.get("hero_file"):
        apply_config(core.load_config(name))
        tries = ((row.get(cfg.get("gsi_name")) or {}).get("tries", 0)) + 1
        log("обход: беру %s, попытка %d (сложность %s, прошли %d, "
            "в очереди %d, без точки пика %d)"
            % (name, tries, diff, ok,
               sum(1 for v in core.hero_files()
                   if sweep_ready(v, cfg.get("screen_file") or "")[0]) - ok,
               len(skipped)))
    return name


def sweep_record(st):
    if (cfg.get("mode") or "solo") != "sweep":
        return
    need = (cfg.get("sweep") or {}).get("pass_wave", 100)
    waves = st.get("wave", 0)
    diff = cfg.get("difficulty", "")
    hero = cfg.get("gsi_name") or ""
    if not hero:
        return
    passed = waves >= need
    if core.mark_result(diff, hero, cfg.get("hero_file"), waves, passed):
        log("обход: %s %s сложность %s (волн %d из %d) — %s"
            % (cfg.get("hero_file"), "прошёл" if passed else "НЕ прошёл",
               diff, waves, need,
               "больше не беру" if passed
               else "иду дальше, вернусь к нему следующим кругом"))


def reload_if_changed():
    try:
        new = core.load_config()
    except Exception as e:
        log("настройки не перечитаны: %s" % e)
        return
    if config_sign(new) == CONFIG_SIGN:
        return
    was = (cfg.get("hero_file"), cfg.get("difficulty"))
    apply_config(new)
    now = (cfg.get("hero_file"), cfg.get("difficulty"))
    if was != now:
        log("настройки сменились: билд %s -> %s, сложность %s -> %s"
            % (was[0], now[0], was[1], now[1]))
    else:
        log("настройки перечитаны")


SCREEN_TIMEOUT = 120
PREGAME_WAIT = 120


def wait_screen(pick, ready, label):
    if ready:
        if not CO.get(ready["point"]):
            return True
        return core.wait_color(*CO[ready["point"]], color=ready["color"],
                               min_frac=ready["min_frac"], w=ready["w"],
                               h=ready["h"], timeout=SCREEN_TIMEOUT)
    return core.wait_rendered(*CO[pick], timeout=SCREEN_TIMEOUT,
                              stable_s=1.5, min_wait=3.0)


def run_pregame(gsi):
    for pick, confirm, label, settle, tries, ready in PREGAME:
        if gsi.state == "GAME_IN_PROGRESS":
            return
        if party_on() and not party_step(pick, label):
            continue
        if not CO.get(pick):
            log("нет координат (%s) — сделай сам: %s" % (pick, label))
            continue

        before = gsi.state
        for attempt in range(1, tries + 1):
            guard()
            if not wait_screen(pick, ready, label):
                log("%s: экран так и не появился" % label)
                break
            time.sleep(settle)
            if pick == "server_pick":
                set_lobby_mode()
            log("%s   (сейчас состояние %s)" % (label, before or "?"))
            with core.INPUT_LOCK:
                core.click(*CO[pick])
                time.sleep(0.6)
                if CO.get(confirm):
                    core.click(*CO[confirm])
            if pick == "hero_pick" and party_role() == "main":
                PARTY.say("hero")

            if wait_for(lambda: gsi.state != before, 25, "переход после «%s»" % label):
                log("   -> состояние стало %s" % gsi.state)
                break

            if ready and CO.get(ready["point"]):
                alive = core.color_frac(
                    core._patch(*CO[ready["point"]], ready["w"], ready["h"]),
                    ready["color"]) >= ready["min_frac"]
                if not alive:
                    log("   окно уже не активно — жду без повторного клика")
                    if wait_for(lambda: gsi.state != before,
                                PREGAME_WAIT, "переход после «%s»" % label):
                        log("   -> состояние стало %s" % gsi.state)
                    break
            log("   состояние не сменилось, повтор %d из %d" % (attempt, tries))

        if party_on() and pick == "hero_pick" and party_role() == "main":
            PARTY.say("hero")


def play_one_game(gsi):
    party_ready()
    if not launch_game(gsi):
        return False

    party_sync()
    run_pregame(gsi)

    if not wait_for(lambda: gsi.state == "GAME_IN_PROGRESS", 300, "старт матча"):
        return False
    run_match(gsi)
    return True


TOTALS = {"matches": 0, "waves": 0, "bosses": 0, "bkb": 0, "deaths": 0,
          "secs": 0}


def match_summary(st):
    secs = round(time.time() - st.get("t0", time.time()))
    TOTALS["matches"] += 1
    TOTALS["waves"] += st.get("wave", 0)
    TOTALS["bosses"] += st.get("bosses", 0)
    TOTALS["bkb"] += st.get("bkb_uses", 0)
    TOTALS["deaths"] += st.get("deaths", 0)
    TOTALS["secs"] += secs
    log("ИТОГ МАТЧА: длительность %d:%02d, волн %d, боссов %d, BKB %d "
        "(пропущено по откату %d), смертей %d, сатаник %d, талантов %d из %d, "
        "предметов %d из %d, уровень %d, конец: %s"
        % (secs // 60, secs % 60, st.get("wave", 0), st.get("bosses", 0),
           st.get("bkb_uses", 0), st.get("bkb_skipped", 0),
           st.get("deaths", 0), st.get("satanic", 0),
           st.get("learned", 0), len(STEPS),
           st.get("bought", 0), len(ITEMS["order"]),
           st.get("level", 0),
           ("НЕ ТОТ ГЕРОЙ (%s)" % st["wrong_hero"]) if st.get("wrong_hero")
           else st.get("why", "?")))
    if st.get("ult_uses") or st.get("ult_skipped"):
        log("ульта за матч: нажата %d раз, пропущено %d, "
            "рефрешеров %d"
            % (st.get("ult_uses", 0), st.get("ult_skipped", 0),
               st.get("refresh_uses", 0)))
    log("ВСЕГО ЗА ПРОГОН: матчей %d, волн %d, боссов %d, BKB %d, смертей %d, "
        "в игре %d:%02d"
        % (TOTALS["matches"], TOTALS["waves"], TOTALS["bosses"],
           TOTALS["bkb"], TOTALS["deaths"],
           TOTALS["secs"] // 3600, TOTALS["secs"] % 3600 // 60))
    sweep_record(st)


def run_match(gsi, done=0, bought=0):
    log("матч пошёл")
    st = {"autocast": False, "ability_level": 0, "done": done, "learned": 0,
          "t0": time.time(), "bought": bought}
    st["peer_cycle"] = PARTY.peer_cycle if party_on() else None
    announced = False
    st["gsi"] = gsi
    stop = threading.Event()
    if WAVE.get("enabled", True):
        threading.Thread(target=wave_watcher, args=(gsi, st, stop), daemon=True).start()
        threading.Thread(target=bar_watcher, args=(gsi, st, stop), daemon=True).start()
        threading.Thread(target=fast_watcher, args=(gsi, st, stop), daemon=True).start()
    try:
        _match_loop(gsi, st, bought, announced)
    finally:
        stop.set()
        match_summary(st)
        if st.get("party_lost"):
            leave_match(st["party_lost"])


def match_over(gsi, st):
    if st.get("party_lost"):
        return "напарник выпал: %s" % st["party_lost"]
    if gsi.in_menu:
        return "вернулись в лобби"
    if not gsi.fresh:
        return "данные GSI пропали"
    if (gsi.get("map.win_team") or "none") != "none":
        return "объявлен победитель (%s)" % gsi.get("map.win_team")

    state = gsi.state
    if state and state != "GAME_IN_PROGRESS":
        st["off_state"] = st.get("off_state", 0) + 1
        if st["off_state"] >= 3:
            return "состояние стало %s" % state
    else:
        st["off_state"] = 0

    mark = (st.get("wave", 0), gsi.get("player.gold", 0))
    if mark != st.get("last_mark"):
        st["last_mark"] = mark
        st["still_since"] = time.time()
    elif time.time() - st.get("still_since", time.time()) > WATCH.get("stall_s", 180):
        return "ничего не меняется %d с" % WATCH.get("stall_s", 180)
    return None


def _match_loop(gsi, st, bought, announced):
    while True:
        guard()
        watchdog(gsi)
        why = match_over(gsi, st)
        if why:
            st["why"] = why
            log("матч закончился: %s" % why)
            return
        if not core.focus_dota():
            time.sleep(1)
            continue

        lvl = gsi.get("hero.level", 0) or 0
        if lvl:
            st["level"] = lvl
        st["bought"] = bought

        try:
            if level_abilities(gsi, st):
                continue
            if right_hero(gsi, st):
                spend_talents(gsi, st)
            bought = buy_items(gsi, bought, st)
            if bought >= len(ITEMS["order"]):
                if not st.get("late_done"):
                    st["late_done"] = buy_late(gsi, st)
                elif farm_books(gsi, st):
                    continue
        except (Abort, Fatal):
            raise
        except Exception as e:
            log("СБОЙ в цикле матча: %s: %s — продолжаю" % (type(e).__name__, e))
            traceback.print_exc()
            time.sleep(2)
            continue

        if time.time() - st.get("beat", 0) > 30:
            st["beat"] = time.time()
            gold = gsi.get("player.gold", 0) or 0
            need_gold = (ITEMS["order"][bought]["cost"]
                         if bought < len(ITEMS["order"]) else END["cost"])
            why = ("шаг %d ждёт нового уровня" % (st["done"] + 1)
                   if st.get("fail") else "идёт работа")
            log("состояние: уровень %d, талантов %d из %d (%s), "
                "предметов %d из %d, золота %d из %d"
                % (lvl, st["learned"], len(STEPS), why,
                   bought, len(ITEMS["order"]), gold, need_gold))

        if (not announced and st["done"] >= len(STEPS)
                and bought >= len(ITEMS["order"])):
            announced = True
            log("план выполнен: талантов %d, предметов %d — дальше способности "
                "и книги" % (st["learned"], bought))
        time.sleep(2)



def check_core_calls():
    import ast
    tree = ast.parse(open(__file__, encoding="utf-8").read())
    names = sorted({n.attr for n in ast.walk(tree)
                    if isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name) and n.value.id == "core"})
    missing = [n for n in names if not hasattr(core, n)]
    if missing:
        print("  НЕТ в core.py: %s" % ", ".join(missing))
    else:
        print("  все %d обращения к core.* на месте" % len(names))
    return not missing


GRID_FILE = core.file("talents_grid.json")


def load_grid():
    try:
        with open(GRID_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print("правила талантов не прочитаны (%s)" % e)
        return None
    cells = (data.get("heroes") or {}).get(cfg.get("gsi_name") or "")
    if not cells:
        return None
    return {"cells": cells, "row_need": data.get("row_need") or []}


def plan_trouble(steps, grid):
    cells, need = grid["cells"], grid["row_need"]
    bymod = {v["mod"]: k for k, v in cells.items()}
    lvl = {k: 0 for k in cells}
    cnt = {1: 0, 2: 0, 3: 0}
    for i, (t, r, c, rank, of) in enumerate(steps, 1):
        k = "%d,%d,%d" % (t, r, c)
        v = cells.get(k)
        if v is None:
            return i, "ячейка %s пустая — там нет таланта" % k
        if lvl[k] >= v["max"]:
            return i, "у «%s» всего %d ранг(ов), а берём больше" % (v["icon"], v["max"])
        if cnt[t] < need[r - 1]:
            return i, ("ряд %d дерева «%s» открывается с %d рангов в нём, а есть %d"
                       % (r, TREE_NAME[t], need[r - 1], cnt[t]))
        req = v.get("req")
        if req:
            have = lvl.get(bymod.get(req[0]), None)
            if have != req[1]:
                return i, ("«%s» требует «%s» ровно %d ранга, а есть %s"
                           % (v["icon"], req[0], req[1], have))
        lvl[k] += 1
        cnt[t] += 1
    return None


def tight_rows(steps, grid):
    need = grid["row_need"]
    out = []
    seen = set()
    cnt = {1: 0, 2: 0, 3: 0}
    for i, (t, r, c, rank, of) in enumerate(steps, 1):
        if r > 1 and (t, r) not in seen:
            seen.add((t, r))
            if cnt[t] == need[r - 1]:
                out.append("%d (%s ряд %d)" % (i, TREE_NAME[t], r))
        cnt[t] += 1
    return out


def check_plan():
    grid = load_grid()
    if not grid:
        print("\nплан не проверен: в talents_grid.json нет правил для %s. "
              "Снять их: py grab_grid.py %s"
              % (cfg.get("gsi_name", "?"), cfg.get("gsi_name", "")))
        return
    bad = plan_trouble(STEPS, grid)
    if bad:
        print("\nПЛАН НЕ ПРОЙДЁТ: шаг %d — %s" % bad)
        print("дальше не проверяю: каждый следующий шаг опирается на этот")
        return
    cnt = collections.Counter(t for t, r, c, rank, of in STEPS)
    print("\nплан сверен с правилами аддона: проходит целиком")
    print("  итог: %s" % ", ".join("%s %d" % (TREE_NAME[t], cnt[t])
                                   for t in (1, 2, 3) if cnt[t]))
    tight = tight_rows(STEPS, grid)
    if tight:
        print("  впритык по порогу ряда: шаг %s" % ", шаг ".join(tight))
        print("  не ошибка, но один потерянный клик раньше — и план встанет тут")


def mode_check():
    print("экран: %dx%d, профиль screens/%s.json"
          % (core.SCREEN_W, core.SCREEN_H, cfg.get("screen_file", "?")))
    if list(cfg.get("resolution") or []) != [core.SCREEN_W, core.SCREEN_H]:
        print("  ВНИМАНИЕ: профиль рассчитан на %s — координаты не совпадут. "
              "Поменяй screen в config.json или поставь auto."
              % (cfg.get("resolution"),))
    print("  другие профили: %s"
          % (", ".join(n for n in core.screen_files()
                       if n != cfg.get("screen_file")) or "нет"))
    print("окно Dota: %s" % ("найдено" if core.find_dota() else "НЕ НАЙДЕНО"))
    print("билд: %s  (heroes/%s.json, в игре ждём %s)"
          % (cfg.get("title", "?"), cfg.get("hero_file", "?"),
             cfg.get("gsi_name", "?")))
    print("  другие билды: %s"
          % (", ".join(n for n in core.hero_files()
                       if n != cfg.get("hero_file")) or "нет"))
    off = [k for k, v in (cfg.get("functions") or {}).items() if not v]
    print("  выключено: %s" % (", ".join(off) if off else "ничего"))
    check_core_calls()
    print("\nкликов по плану: %d" % len(STEPS))
    for i, (t, r, c, rank, of) in enumerate(STEPS, 1):
        x, y = cell_xy(t, r, c)
        print("  %2d. %-10s строка %d колонка %d  ранг %d/%d  ->  (%4d, %4d)"
              % (i, TREE_NAME[t], r, c, rank, of, round(x), round(y)))
    print("\nпредметы:")
    for e in ITEMS["order"]:
        print("  %-22s %d золота" % (e["search"], e["cost"]))
    check_plan()
    missing = [k for k, v in CO.items()
               if not k.startswith("_") and (not v or not any(v))]
    print("\nне снятые координаты: %s" % (", ".join(missing) if missing else "нет, всё готово"))


def mode_items(force=False):
    print("Только покупка предметов. Таланты и способности не трогаю.")
    if force:
        print("Режим force: пробую купить, даже если золота не хватает —")
        print("покупка не пройдёт, но будет видно, где ломается магазин.")
    print("Стоп — F12.\n")
    if not countdown():
        return
    gsi = core.GSI(cfg["gsi_port"])
    gsi.start()
    bought = 0
    st = {}
    order = ITEMS["order"]
    try:
        if not wait_for(lambda: gsi.fresh, 60, "данные GSI"):
            return
        while bought < len(order):
            guard()
            if gsi.in_menu or not gsi.fresh:
                log("матч закончился")
                return
            if not core.focus_dota():
                time.sleep(1)
                continue

            e = order[bought]
            gold = gsi.get("player.gold", 0) or 0
            have = count_item(gsi, e["name"])
            log("цель %d из %d: %s  цена %d, золота %d, таких в сумке %d"
                % (bought + 1, len(order), e["search"], e["cost"], gold, have))

            if gold < e["cost"] and not force:
                nap(5, "не хватает %d золота" % (e["cost"] - gold))
                continue

            res = buy(gsi, e)
            if res == "ok":
                bought += 1
                log("КУПЛЕНО %d из %d: %s" % (bought, len(order), e["search"]))
            elif res == "wrong":
                log("СТОП: купилось не то. Поле поиска не очистилось — "
                    "дальше покупать нельзя, сольём золото.")
                return
            else:
                log("не куплено, повтор через 5 с")
                if force:
                    log("(в режиме force это ожидаемо, если золота не хватило)")
                nap(5)
        log("все предметы куплены: %d из %d" % (bought, len(order)))
        book_loop(gsi, st)
    except Abort as ex:
        log("остановлено: %s" % ex)
    finally:
        gsi.stop()


def book_loop(gsi, st):
    if not END.get("enabled"):
        log("докуп книг выключен в конфиге")
        return
    if not CO.get("quickbuy_slot"):
        log("не снята координата quickbuy_slot — книги покупать нечем")
        return
    log("перехожу к книгам: %s по %d" % (END["search"], END["cost"]))
    while True:
        guard()
        if gsi.in_menu or not gsi.fresh:
            log("матч закончился")
            return
        if not core.focus_dota():
            time.sleep(1)
            continue
        if not farm_books(gsi, st):
            time.sleep(5)


def mode_books():
    print("Только книги: кладу %s в квикбай и покупаю на всё золото."
          % END["search"])
    print("Остальное не трогаю. Стоп — F12.\n")
    if not countdown():
        return
    gsi = core.GSI(cfg["gsi_port"])
    gsi.start()
    try:
        if not wait_for(lambda: gsi.fresh, 60, "данные GSI"):
            return
        book_loop(gsi, {})
    except Abort as ex:
        log("остановлено: %s" % ex)
    finally:
        gsi.stop()


def mode_talents():
    print("Только прокачка талантов. Зайди в матч, где есть свободные очки.")
    print("Способности и покупки не трогаю. Стоп — F12.\n")
    if not countdown():
        return
    gsi = core.GSI(cfg["gsi_port"])
    gsi.start()
    st = {"done": 0, "learned": 0}
    done = 0
    try:
        if not wait_for(lambda: gsi.fresh, 60, "данные GSI"):
            return
        while st["done"] < len(STEPS):
            guard()
            if gsi.in_menu or not gsi.fresh:
                log("матч закончился")
                return
            if not core.focus_dota():
                time.sleep(1)
                continue
            lvl = gsi.get("hero.level", 0) or 0
            log("круг: уровень %d, шаг %d из %d, взято %d"
                % (lvl, st["done"] + 1, len(STEPS), st["learned"]))
            before = st["done"]
            spend_talents(gsi, st)
            done = st["done"]
            if done == before:
                log("за круг ничего не прокачалось, жду 5 с")
                time.sleep(5)
        log("план талантов выполнен целиком: %d из %d" % (done, len(STEPS)))
    except Abort as e:
        log("остановлено: %s" % e)
    finally:
        gsi.stop()


def mode_probe():
    pts = [(k, CO[k]) for k in ("server_pick", "server_confirm", "difficulty_pick",
                                "difficulty_confirm", "hero_pick",
                                "hero_pick_confirm") if CO.get(k)]
    print("Зелёный >= 0.18 в server_confirm = окно сервера показано.")
    print("Стоп — F12.\n")
    if not countdown():
        return
    markers = {m["point"]: m for _, _, _, _, _, m in PREGAME if m}
    while not core.abort_pressed():
        print("\033c", end="")
        print("%-20s %-12s %7s %7s %6s  %s"
              % ("точка", "координаты", "зелёный", "золото", "белый", "маркер экрана"))
        for name, xy in pts:
            m = markers.get(name)
            wide = core._patch(*xy, *( (m["w"], m["h"]) if m else (56, 56) ))
            verdict = ""
            if m:
                f = core.color_frac(wide, m["color"])
                verdict = "%s %.2f / порог %.2f  %s" % (
                    m["color"], f, m["min_frac"], "ГОТОВ" if f >= m["min_frac"] else "")
            sq = core._patch(*xy)
            print("%-20s %-12s %7.2f %7.2f %6.2f  %s"
                  % (name, "%d,%d" % tuple(xy),
                     core.color_frac(sq, "green"), core.color_frac(sq, "gold"),
                     core.color_frac(sq, "white"), verdict))
        time.sleep(1.0)


def mode_states():
    gsi = core.GSI(cfg["gsi_port"])
    gsi.start()
    print("Слежу за состоянием игры. Заходи в кастомку обычным способом.")
    print("Каждый раз, когда на экране появляется новый экран, называй его вслух —")
    print("строка перехода покажет, какому состоянию он соответствует.")
    print("Стоп — F12.\n")
    print("%-9s %-7s %-26s %s" % ("время", "в сост.", "game_state", "детали"))
    prev, since, last_tick = None, time.time(), 0.0
    try:
        while not core.abort_pressed():
            time.sleep(0.25)
            if not gsi.fresh:
                cur = "(нет данных)"
            elif gsi.in_menu:
                cur = "MENU"
            else:
                cur = gsi.state or "(нет map)"

            now = time.time()
            if cur != prev:
                d, _ = gsi.snapshot()
                extra = []
                if d.get("hero"):
                    extra.append("герой=%s ур.%s" % ((d["hero"].get("name") or "?")
                                                     .replace("npc_dota_hero_", ""),
                                                     d["hero"].get("level")))
                if d.get("player"):
                    extra.append("золото=%s" % d["player"].get("gold"))
                extra.append("блоки=%d" % len([k for k in d if k != "previously"]))
                held = "" if prev is None else "%5.1fс" % (now - since)
                print("%-9s %-7s %-26s %s"
                      % (time.strftime("%H:%M:%S"), held, cur, "  ".join(extra)))
                prev, since = cur, now
                last_tick = now
            elif now - last_tick >= 5:
                print("%-9s %-7s %-26s ..." % (time.strftime("%H:%M:%S"),
                                               "%5.1fс" % (now - since), cur))
                last_tick = now
    finally:
        gsi.stop()
        print("\nостановлено")


def mode_abilities():
    print("Жду данные GSI. Зайди в матч. Стоп — F12.\n")
    if not countdown():
        return
    gsi = core.GSI(cfg["gsi_port"])
    gsi.start()
    try:
        while not core.abort_pressed():
            if not gsi.fresh:
                time.sleep(0.5)
                continue
            d, _ = gsi.snapshot()
            ab = d.get("abilities") or {}
            if not ab:
                time.sleep(1)
                continue
            slots = ability_slots(gsi)
            rev = {v: k for k, v in slots.items()}
            print("\033c", end="")
            print("способности из GSI:\n")
            for k in sorted(ab, key=lambda s: int(re.sub(r"\D", "", s) or 0)):
                v = ab[k]
                if not isinstance(v, dict):
                    continue
                i = int(re.sub(r"\D", "", k) or 0)
                slot = rev.get(i, "-")
                key = "+".join(AB["keys"][slot]) if slot in AB["keys"] else "-"
                print("  %-10s %-38s ур.%-3s %-7s %-6s %s"
                      % (k, v.get("name", "?"), v.get("level"),
                         "УЛЬТА" if v.get("ultimate") else "", slot, key))
            print("\nприоритет: %s" % " -> ".join(AB["priority"]))
            print("автокаст:  %s на %s" % ("+".join(AB["autocast"]["combo"]),
                                           AB["autocast"]["slot"]))
            time.sleep(1.5)
    finally:
        gsi.stop()


def mode_console():
    key = cfg["launch"]["console_key"]
    cmd = cfg["launch"]["command"]
    print("Проверка захода через консоль.")
    print("  клавиша консоли: %s" % key)
    print("  команда:         %s" % cmd)
    print()
    if not countdown(8, "Выйди в главное меню Dota, мышь не трогай"):
        return
    if not core.focus_dota():
        print("\nокно Dota не найдено")
        return
    core.console_command(cmd, key)
    print("\nкоманда отправлена. Если игра не грузится — смотри, открылась ли консоль:")
    print("  открылась, но команда не ушла -> проблема с вводом текста")
    print("  не открылась вовсе           -> не та клавиша в config.json -> launch.console_key")


def mode_counter():
    print("Открой в игре панель талантов. Я показываю, что вижу в области счётчика")
    print("'Всего Талантов: NN'. Закрыта панель -> 0. Открыта -> стабильное число > 12.")
    print("Прокачай талант руками: число должно измениться. Стоп — F12.\n")
    if not countdown():
        return
    prev = None
    while not core.abort_pressed():
        m = b"".join(tree_mask(t) for t in (1, 2, 3))
        n = sum(m)
        mark = "  <-- ИЗМЕНИЛОСЬ" if (prev is not None and m != prev) else ""
        print("  белых пикселей: %4d   панель: %s%s"
              % (n, "открыта" if n > 12 else "закрыта", mark))
        prev = m
        time.sleep(0.5)


def mode_dry():
    print("Сухой прогон: мышь обойдёт все ячейки плана. Кликов НЕ будет.")
    print("Смотри, попадает ли курсор в центр ячеек. Стоп — F12.\n")
    if not countdown(12, "Переключись в Dota и открой панель талантов"):
        return
    for i, (t, r, c, rank, of) in enumerate(STEPS, 1):
        if core.abort_pressed():
            print("стоп")
            return
        x, y = cell_xy(t, r, c)
        core.move(x, y)
        print("  %2d/%d  %-10s строка %d колонка %d  ->  (%4d, %4d)"
              % (i, len(STEPS), TREE_NAME[t], r, c, round(x), round(y)))
        time.sleep(0.7)
    print("\nготово. Если курсор везде попадал в центр — геометрия верна.")


def mode_run(limit=None):
    if [core.SCREEN_W, core.SCREEN_H] != list(cfg.get("resolution") or []):
        log("ВНИМАНИЕ: экран %dx%d, а профиль screens/%s.json рассчитан на "
            "%s — координаты не совпадут"
            % (core.SCREEN_W, core.SCREEN_H, cfg.get("screen_file", "?"),
               cfg.get("resolution")))
    log("прогон на %s" % ("%d матч(ей)" % limit if limit else "без ограничения"))
    if not countdown(why="Открой Dota и выйди в главное меню"):
        return
    gsi = core.GSI(cfg["gsi_port"])
    try:
        gsi.start()
    except OSError:
        log("ОСТАНОВКА: порт %d занят — похоже, бот уже запущен в другом окне. "
            "Два бота делили бы один лог и мешали друг другу." % cfg["gsi_port"])
        return
    log("слушаю GSI на порту %d, жду данные от Dota" % cfg["gsi_port"])
    party_start()
    try:
        if not wait_for(lambda: gsi.fresh, 10, "данные GSI"):
            if globals().get("GSI_READY", True):
                log("данных GSI нет — похоже, мы в меню, захожу в игру")
            else:
                log("данных GSI нет, и конфиг только что положен на "
                    "место — Dota его ещё не читала. Перезапусти "
                    "игру, иначе бот так и будет стоять на первом "
                    "экране.")
        fails = 0
        stuck_since = None
        stuck_restarts = 0
        st_once = {}
        played = 0
        while True:
            guard()
            watchdog()
            if gsi.in_menu or not gsi.state:
                stuck_since = None
                st_once.clear()
                reload_if_changed()
                party_refresh()
                sweep_pick()
                if play_one_game(gsi):
                    fails = 0
                    stuck_restarts = 0
                    played += 1
                    log("сыграно матчей: %d%s"
                        % (played, " из %d" % limit if limit else ""))
                    if limit and played >= limit:
                        log("прогон окончен: сыграно %d матч(ей)" % played)
                        return
                else:
                    fails += 1
                    max_fails = WATCH.get("max_launch_fails", 3)
                    log("зайти в игру не удалось (%d из %d)" % (fails, max_fails))
                    if fails >= max_fails:
                        raise Fatal("не смог зайти в кастомку %d раза подряд" % fails)
                pause = WATCH.get("between_matches_s", 30)
                log("пауза %d секунд перед следующей партией" % pause)
                time.sleep(pause)
            elif gsi.state == "GAME_IN_PROGRESS":
                stuck_since = None
                st_once.clear()
                log("матч уже идёт — подключаюсь к нему")
                run_match(gsi)
                played += 1
                log("сыграно матчей: %d%s"
                    % (played, " из %d" % limit if limit else ""))
                if limit and played >= limit:
                    log("прогон окончен: сыграно %d матч(ей)" % played)
                    return
            else:
                if stuck_since is None:
                    stuck_since = time.time()
                    log("экран %s — жду" % gsi.state)
                held = time.time() - stuck_since
                if held > WATCH.get("lobby_stuck_s", 40) and not st_once.get("said"):
                    st_once["said"] = True
                    log("вишу в лобби уже %d с — жду ещё, потом перезапущу "
                        "игру" % held)
                wait_max = WATCH.get("lobby_restart_s", 300)
                if wait_max and held > wait_max:
                    stuck_since = None
                    st_once.clear()
                    stuck_restarts += 1
                    top = WATCH.get("lobby_restart_max", 3)
                    if stuck_restarts > top:
                        raise Fatal("лобби зависло снова после %d "
                                    "перезапусков — останавливаюсь"
                                    % top)
                    log("вишу на экране %s уже %s — перезапускаю Dota "
                        "(попытка %d из %d)"
                        % (gsi.state or "?",
                           ("%d мин" % round(held / 60)) if held >= 90
                           else ("%d с" % round(held)),
                           stuck_restarts, top))
                    log(machine.restart_dota())
                    gsi.forget()
                    nap(WATCH.get("after_restart_s", 60),
                        "даю игре подняться и выйти в меню")
                    log("игра поднята — захожу в кастомку заново")
                    continue
                time.sleep(2)
    except Fatal as e:
        log("ОСТАНОВКА: %s" % e)
    except Abort as e:
        log("остановлено: %s" % e)
    except KeyboardInterrupt:
        log("остановлено с клавиатуры")
    finally:
        party_stop()
        gsi.stop()


def run_mode(arg):
    if arg == "--check":
        mode_check()
    elif arg == "--dry":
        mode_dry()
    elif arg == "--counter":
        mode_counter()
    elif arg == "--console":
        mode_console()
    elif arg == "--abilities":
        mode_abilities()
    elif arg == "--states":
        mode_states()
    elif arg == "--probe":
        mode_probe()
    elif arg == "--talents":
        mode_talents()
    elif arg == "--items":
        mode_items(force="force" in sys.argv[2:])
    elif arg == "--books":
        mode_books()
    elif arg.isdigit():
        mode_run(int(arg))
    else:
        mode_run()


def startup_notes():
    scr = core.pick_screen(cfg.get("screen"))
    log("экран %dx%d, профиль screens/%s.json, герой %s, сложность %s"
        % (core.SCREEN_W, core.SCREEN_H, scr, cfg.get("hero"),
           cfg.get("difficulty")))
    items = checks.checklist(cfg)
    globals()["GSI_READY"] = next((ok for key, ok, _ in items
                                   if key == "gsi"), True)
    for key, ok, note in items:
        if ok:
            log("сделал за тебя: %s" % note)
    need = [note for key, ok, note in items if not ok]
    if need:
        log("ПЕРЕД СТАРТОМ НАДО ПОПРАВИТЬ:")
        for note in need:
            log("   - %s" % note)
    else:
        log("проверка настроек: всё на месте")
    stash = [e["search"] for e in ITEMS.get("order") or [] if e.get("stash")]
    if stash and not CO.get("stash_button"):
        log("   - %s надо убирать в тайник, а кнопка «в тайник» не снята — "
            "сними её на пульте" % ", ".join(stash))


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    log("=" * 20 + " запуск: %s " % (arg or "боевой режим") + "=" * 20)
    startup_notes()
    try:
        run_mode(arg)
    except KeyboardInterrupt:
        log("ЗАВЕРШЕНИЕ: Ctrl+C")
    except BaseException as e:
        log("ЗАВЕРШЕНИЕ ПО ОШИБКЕ: %s: %s" % (type(e).__name__, e))
        log(traceback.format_exc())
        raise
    else:
        log("ЗАВЕРШЕНИЕ: штатное")

if __name__ == "__main__":
    main()
