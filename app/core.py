"""
Общий слой для авто-игры: ввод, экран, GSI.

Ничего не знает про таланты и предметы — только примитивы.
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageChops, ImageGrab, ImageStat

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CODE = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):
    HERE = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(CODE)

SETTINGS_DIR = os.path.join(HERE, "settings")
DATA_DIR = os.path.join(HERE, "data")
LOGS_DIR = os.path.join(HERE, "logs")
GAMEFILES_DIR = os.path.join(HERE, "gamefiles")

PLACES = {
    "config.json": SETTINGS_DIR,
    "local.json": SETTINGS_DIR,
    "local.example.json": SETTINGS_DIR,
    "talents_grid.json": DATA_DIR,
    "gamedata.json": DATA_DIR,
    "creeps.json": DATA_DIR,
    "prices.json": DATA_DIR,
    "gamestate_integration_wodota.cfg": DATA_DIR,
    "hud_drop.png": DATA_DIR,
    "hud_drop_2k.png": DATA_DIR,
    "bot.log": LOGS_DIR,
    "progress.json": LOGS_DIR,
    "party_state.json": LOGS_DIR,
    "remote_shot.jpg": LOGS_DIR,
}


def file(name):
    return os.path.join(PLACES.get(name, HERE), name)


def ensure_dirs():
    for d in (SETTINGS_DIR, DATA_DIR, LOGS_DIR):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


ensure_dirs()



def make_dpi_aware():
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()


make_dpi_aware()

user32 = ctypes.windll.user32
SCREEN_W = user32.GetSystemMetrics(0)
SCREEN_H = user32.GetSystemMetrics(1)



INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE = 0x0001, 0x8000
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
}
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x0002, 0x0004, 0x0008


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


INPUT_LOCK = threading.RLock()


def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _mouse(flags, x=0, y=0):
    return INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(x, y, 0, flags, 0, None))


def _key(scan, flags):
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, scan, flags, 0, None))


def move(x, y):
    x, y = int(round(x)), int(round(y))
    user32.SetCursorPos(x, y)
    _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
                 int(x * 65535 / (SCREEN_W - 1)), int(y * 65535 / (SCREEN_H - 1))))


def cursor_pos():
    class P(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    p = P()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def glide(x, y, dt=0.008):
    x0, y0 = cursor_pos()
    dist = max(abs(x - x0), abs(y - y0))
    steps = max(3, min(14, int(dist / 60) + 3))
    for i in range(1, steps + 1):
        k = i / steps
        k = k * k * (3 - 2 * k)
        move(x0 + (x - x0) * k, y0 + (y - y0) * k)
        time.sleep(dt)


def click(x, y, settle=0.06, button="left", mods=()):
    down, up = BUTTONS[button]
    specs = [resolve_key(m) for m in mods]
    glide(x, y)
    time.sleep(settle)
    for scan, ext in specs:
        _send(_key(scan, KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if ext else 0)))
        time.sleep(0.03)
    _send(_mouse(down))
    time.sleep(0.03)
    _send(_mouse(up))
    for scan, ext in reversed(specs):
        time.sleep(0.03)
        _send(_key(scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP
                   | (KEYEVENTF_EXTENDEDKEY if ext else 0)))


KEYEVENTF_EXTENDEDKEY = 0x0001

KEYS = {
    "grave":        (0x29, False),
    "enter":        (0x1C, False),
    "numpad_enter": (0x1C, True),
    "escape":       (0x01, False),
    "space":        (0x39, False),
    "f1": (0x3B, False), "f2": (0x3C, False), "f3": (0x3D, False),
    "f4": (0x3E, False), "f5": (0x3F, False), "f6": (0x40, False),
    "f7": (0x41, False), "f8": (0x42, False), "f9": (0x43, False),
    "f10": (0x44, False), "f11": (0x57, False), "f12": (0x58, False),
    "insert": (0x52, True), "delete": (0x53, True), "home": (0x47, True),
    "end": (0x4F, True), "pageup": (0x49, True), "pagedown": (0x51, True),
    "ctrl": (0x1D, False), "shift": (0x2A, False), "alt": (0x38, False),
    "backspace": (0x0E, False), "tab": (0x0F, False),
}
for _i, _ch in enumerate("qwertyuiop"):
    KEYS[_ch] = (0x10 + _i, False)
for _i, _ch in enumerate("asdfghjkl"):
    KEYS[_ch] = (0x1E + _i, False)
for _i, _ch in enumerate("zxcvbnm"):
    KEYS[_ch] = (0x2C + _i, False)
for _i, _ch in enumerate("1234567890"):
    KEYS[_ch] = (0x02 + _i, False)


def resolve_key(spec):
    if isinstance(spec, str):
        if spec.lower() not in KEYS:
            raise ValueError("неизвестная клавиша: %s (есть: %s)"
                             % (spec, ", ".join(sorted(KEYS))))
        return KEYS[spec.lower()]
    if isinstance(spec, (list, tuple)):
        return int(spec[0]), bool(spec[1])
    return int(spec), False


def tap(scancode, hold=0.04, extended=False):
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    _send(_key(scancode, flags))
    time.sleep(hold)
    _send(_key(scancode, flags | KEYEVENTF_KEYUP))


SC_ENTER, SC_ESC, SC_GRAVE, SC_CTRL, SC_A = 0x1C, 0x01, 0x29, 0x1D, 0x1E


def drag(x1, y1, x2, y2, settle=0.05, steps=8):
    glide(x1, y1)
    time.sleep(settle)
    _send(_mouse(MOUSEEVENTF_LEFTDOWN))
    time.sleep(settle)
    for i in range(1, steps + 1):
        move(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
        time.sleep(0.015)
    time.sleep(settle)
    _send(_mouse(MOUSEEVENTF_LEFTUP))
    time.sleep(settle)


def chord(*keys, hold=0.045):
    specs = [resolve_key(k) for k in keys]
    flags = lambda ext: KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if ext else 0)
    for scan, ext in specs[:-1]:
        _send(_key(scan, flags(ext)))
        time.sleep(hold)
    scan, ext = specs[-1]
    _send(_key(scan, flags(ext)))
    time.sleep(hold)
    _send(_key(scan, flags(ext) | KEYEVENTF_KEYUP))
    for scan, ext in reversed(specs[:-1]):
        time.sleep(hold)
        _send(_key(scan, flags(ext) | KEYEVENTF_KEYUP))


def type_text(s, per_char=0.012):
    for ch in s:
        code = ord(ch)
        _send(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, None)))
        _send(INPUT(type=INPUT_KEYBOARD,
                    ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)))
        time.sleep(per_char)



def window_exe(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = wt.HANDLE
    k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    h = k32.OpenProcess(0x1000, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        k32.CloseHandle(h)


def find_dota():
    for finder in (lambda: user32.FindWindowW("SDL_app", "Dota 2"),
                   lambda: user32.FindWindowW(None, "Dota 2"),
                   lambda: user32.FindWindowW("SDL_app", None)):
        try:
            h = finder()
            if h and window_exe(h) == "dota2.exe":
                return h
        except Exception:
            pass
    return 0


def focus_dota():
    h = find_dota()
    if not h:
        return False
    if user32.GetForegroundWindow() == h:
        return True
    user32.ShowWindow(h, 9)
    user32.SetForegroundWindow(h)
    time.sleep(0.25)
    return user32.GetForegroundWindow() == h


def console_command(cmd, key="grave"):
    scan, ext = resolve_key(key)
    tap(scan, extended=ext)
    time.sleep(0.6)
    type_text(cmd)
    time.sleep(0.2)
    tap(SC_ENTER)
    time.sleep(0.4)
    tap(scan, extended=ext)
    time.sleep(0.2)



def grab(box):
    return ImageGrab.grab(bbox=tuple(box))


def white_mask(box, thr=215):
    im = grab(box).convert("RGB")
    w, h = im.size
    px = im.load()
    bits = bytearray()
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            bits.append(1 if (r > thr and g > thr and b > thr) else 0)
    return bytes(bits)


def _patch(x, y, w=56, h=None):
    h = w if h is None else h
    return [max(0, int(x) - w // 2), max(0, int(y) - h // 2),
            min(SCREEN_W, int(x) + w // 2), min(SCREEN_H, int(y) + h // 2)]


def _sample(box, step=2):
    im = grab(box).convert("L")
    w, h = im.size
    px = im.load()
    return [px[x, y] for y in range(0, h, step) for x in range(0, w, step)]


def _std(v):
    m = sum(v) / len(v)
    return (sum((p - m) ** 2 for p in v) / len(v)) ** 0.5


def _diff(a, b):
    return sum(abs(p - q) for p, q in zip(a, b)) / len(a)


def wait_rendered(x, y, timeout=90, min_std=7.0, stable_s=0.8, min_wait=1.5):
    box = _patch(x, y)
    t0 = time.time()
    deadline = t0 + timeout
    base = _sample(box)
    prev, stable_from = None, None
    while time.time() < deadline:
        cur = _sample(box)
        drawn = _std(cur) >= min_std
        still = prev is not None and _diff(cur, prev) < 2.5
        if drawn and still:
            stable_from = stable_from or time.time()
            held = time.time() - stable_from
            waited = time.time() - t0
            appeared = _diff(cur, base) > 6.0
            if held >= stable_s and waited >= min_wait and (appeared or waited >= 6.0):
                return True
        else:
            stable_from = None
        prev = cur
        time.sleep(0.2)
    return False


COLORS = {
    "green": lambda r, g, b: g > 110 and g - r > 30 and g - b > 25,
    "gold": lambda r, g, b: r > 170 and g > 130 and b < 110 and r - b > 90 and r - g > 25,
    "yellow": lambda r, g, b: r > 150 and g > 110 and b < 110 and r - b > 70,
    "white": lambda r, g, b: r > 215 and g > 215 and b > 215,
    "dark_red": lambda r, g, b: 45 < r < 180 and r - g > 12 and r - b > 12,
    "icon_red": lambda r, g, b: r > 120 and r - g > 60 and r - b > 60,
    "icon_green": lambda r, g, b: g > 120 and g - r > 60 and g - b > 60,
    "icon_cyan": lambda r, g, b: b > 120 and g > 100 and b - r > 60,
}


def color_frac(box, color, step=2):
    test = COLORS[color]
    im = grab(box).convert("RGB")
    w, h = im.size
    px = im.load()
    hit = tot = 0
    for y in range(0, h, step):
        for x in range(0, w, step):
            tot += 1
            if test(*px[x, y]):
                hit += 1
    return hit / max(1, tot)


def wait_color(x, y, color="green", min_frac=0.18, timeout=120, stable_s=0.5,
               w=56, h=56):
    box = _patch(x, y, w, h)
    deadline = time.time() + timeout
    since = None
    while time.time() < deadline:
        if color_frac(box, color) >= min_frac:
            since = since or time.time()
            if time.time() - since >= stable_s:
                return True
        else:
            since = None
        time.sleep(0.2)
    return False


def save_shot(name, box=None, quality=70):
    try:
        path = os.path.join(LOGS_DIR, name)
        img = grab(box or [0, 0, SCREEN_W, SCREEN_H])
        if path.lower().endswith((".jpg", ".jpeg")):
            img.convert("RGB").save(path, "JPEG", quality=quality)
        else:
            img.save(path)
        return path
    except Exception as e:
        log("не удалось снять экран: %s" % e)
        return None


def mean_bright(x, y, size=28):
    v = _sample(_patch(x, y, size))
    return sum(v) / len(v)


def wait_change(box, before, timeout_ms, poll_ms=90):
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        time.sleep(poll_ms / 1000.0)
        if white_mask(box) != before:
            return True
    return False



class GSI:

    def __init__(self, port=3100):
        self.port = port
        self._lock = threading.Lock()
        self._data = {}
        self._stamp = 0.0
        self._srv = None

    def start(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
                try:
                    d = json.loads(raw.decode("utf-8"))
                except Exception:
                    return
                with outer._lock:
                    outer._data = d
                    outer._stamp = time.time()

        H.server_version = "wodota"
        ThreadingHTTPServer.allow_reuse_address = False
        self._srv = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def stop(self):
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()

    def forget(self):
        with self._lock:
            self._data = {}
            self._stamp = 0.0

    def snapshot(self):
        with self._lock:
            return dict(self._data), self._stamp

    def get(self, path, default=None):
        d, _ = self.snapshot()
        cur = d
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur if cur is not None else default

    @property
    def age(self):
        return (time.time() - self._stamp) if self._stamp else float("inf")

    @property
    def fresh(self):
        return self.age < 5.0

    @property
    def in_menu(self):
        d, _ = self.snapshot()
        return bool(d) and "map" not in d and "hero" not in d

    @property
    def state(self):
        return (self.get("map.game_state") or "").replace("DOTA_GAMERULES_STATE_", "")



def load_creeps():
    try:
        with open(file("creeps.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


HEROES_DIR = os.path.join(HERE, "heroes")
SCREENS_DIR = os.path.join(HERE, "screens")


def screen_files():
    try:
        return sorted(n[:-5] for n in os.listdir(SCREENS_DIR)
                      if n.endswith(".json"))
    except OSError:
        return []


def screen_path(name):
    return os.path.join(SCREENS_DIR, "%s.json" % os.path.basename(name))


def pick_screen(want):
    here = "%dx%d" % (SCREEN_W, SCREEN_H)
    have = screen_files()
    if want and want != "auto" and want in have:
        return want
    if here in have:
        return here
    return want if want in have else (have[0] if have else "")


def hero_files():
    try:
        return sorted(n[:-5] for n in os.listdir(HEROES_DIR)
                      if n.endswith(".json"))
    except OSError:
        return []


def hero_path(name):
    return os.path.join(HEROES_DIR, "%s.json" % os.path.basename(name))


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {} if default is None else default


def merge(base, over):
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


LOCAL_FILE = file("local.json")

PROGRESS_FILE = file("progress.json")


def progress():
    return read_json(PROGRESS_FILE, {})


def mark_result(difficulty, hero, build, waves, passed):
    data = progress()
    key = str(difficulty)
    row = data.setdefault(key, {})
    was = row.get(hero) or {}
    row[hero] = {
        "build": build, "waves": max(int(waves), was.get("waves", 0)),
        "passed": bool(passed), "tries": was.get("tries", 0) + 1,
        "at": time.strftime("%d.%m.%Y %H:%M")}
    try:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        return True
    except Exception as e:
        log("прогресс записать не удалось: %s" % e)
        return False


def forget_result(difficulty, hero):
    data = progress()
    row = data.get(str(difficulty)) or {}
    if hero not in row:
        return False
    del row[hero]
    try:
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        return True
    except Exception as e:
        log("прогресс записать не удалось: %s" % e)
        return False


def load_config(hero=None):
    cfg = read_json(file("config.json"))
    cfg = merge(cfg, read_json(LOCAL_FILE) or {})

    scr = pick_screen(cfg.get("screen"))
    if scr:
        data = read_json(screen_path(scr))
        if data:
            cfg = merge(cfg, data)
            cfg["screen_file"] = scr
        else:
            log("профиль экрана screens/%s.json не прочитан" % scr)

    name = hero or cfg.get("hero")
    if not name:
        return cfg
    hero = read_json(hero_path(name))
    if not hero:
        log("билд heroes/%s.json не прочитан — работаю на общих настройках"
            % name)
        return cfg
    cfg = merge(cfg, hero)
    cfg["hero_file"] = name
    at = cfg.get("pick_at")
    if isinstance(at, dict):
        cfg["pick_at"] = at.get(cfg.get("screen_file")) or []
    return cfg



GAMEDATA_FILE = file("gamedata.json")
PRICES_FILE = file("prices.json")
_cache = {}


def gamedata():
    if "game" not in _cache:
        _cache["game"] = read_json(GAMEDATA_FILE, {})
    return _cache["game"]


def item_info(name):
    for i in gamedata().get("items", []):
        if i["name"] == name:
            return i
    return {}


def item_title(name):
    return item_info(name).get("title") or name.replace("item_", "")


def prices():
    if "prices" not in _cache:
        _cache["prices"] = read_json(PRICES_FILE, {})
    return _cache["prices"]


def learn_price(name, spent):
    if spent < 100:
        return
    p = prices()
    old = p.get(name, {}).get("cost", 0)
    if spent <= old:
        return
    p[name] = {"cost": int(spent), "at": time.strftime("%d.%m.%Y %H:%M")}
    try:
        with open(PRICES_FILE, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=1, sort_keys=True)
    except Exception as e:
        log("цену записать не удалось: %s" % e)


def item_cost(entry):
    if entry.get("cost"):
        return int(entry["cost"])
    name = entry.get("name", "")
    learned = prices().get(name, {}).get("cost")
    if learned:
        return int(learned)
    return int(item_info(name).get("cost") or 0)


def resolve_items(cfg):
    roles = cfg.get("roles") or {}
    plain, late, books = [], [], None
    for e in cfg.get("items") or []:
        if not e.get("name"):
            continue
        item = dict(e)
        item["search"] = e.get("search") or item_title(e["name"])
        item["cost"] = item_cost(e)
        role = e.get("role") or ""
        item["role"] = role
        if role == "books":
            books = item
        elif role in roles:
            late.append(item)
        else:
            plain.append(item)
    return plain, late, books


def abort_pressed(vk=0x7B):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


LOG_FILE = file("bot.log")


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def copy_clipboard(text):
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
    k32 = ctypes.windll.kernel32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    buf = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buf)
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    handle = None
    try:
        user32.EmptyClipboard()
        handle = k32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        dst = k32.GlobalLock(handle)
        if not dst:
            return False
        ctypes.memmove(dst, buf, size)
        k32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            return False
        handle = None
        return True
    finally:
        if handle:
            k32.GlobalFree(handle)
        user32.CloseClipboard()


def avg_color(box):
    st = ImageStat.Stat(grab(box).convert("RGB"))
    return tuple(int(v) for v in st.mean[:3])


def patch(box):
    return grab(box).convert("RGB")


_TEMPLATES = {}


def template(name, scale=1.0):
    key = (name, round(scale, 3))
    if key not in _TEMPLATES:
        im = Image.open(file(name)).convert("L")
        if abs(scale - 1.0) > 0.01:
            side = (int(round(im.width * scale)), int(round(im.height * scale)))
            im = im.resize(side, Image.LANCZOS)
        _TEMPLATES[key] = im
    return _TEMPLATES[key]


def _scan(field, tpl, step):
    tw, th = tpl.size
    best = None
    for y in range(0, field.size[1] - th + 1, step):
        for x in range(0, field.size[0] - tw + 1, step):
            d = ImageStat.Stat(
                ImageChops.difference(field.crop((x, y, x + tw, y + th)), tpl)
            ).mean[0]
            if best is None or d < best[2]:
                best = (x, y, d)
    return best


def find_patch(tpl, box, step=2, good=16):
    field = grab(box).convert("L")
    best = _scan(field, tpl, step)
    if best and best[2] > good:
        shift = int(round(ImageStat.Stat(field).mean[0]
                          - ImageStat.Stat(tpl).mean[0]))
        if shift:
            dim = tpl.point(lambda v, s=shift: max(0, min(255, v + s)))
            other = _scan(field, dim, step)
            if other and other[2] < best[2]:
                best = other
    if best is None:
        return None
    tw, th = tpl.size
    return box[0] + best[0] + tw // 2, box[1] + best[1] + th // 2, best[2]


def patch_diff(a, b):
    st = ImageStat.Stat(ImageChops.difference(a, b))
    return sum(st.mean) / len(st.mean)
