"""
Пульт с телефона: снимок экрана, статистика, логи, стоп и перезапуск бота.

Запускать вместо bot.py — пульт сам поднимает бота дочерним процессом
и может его останавливать и запускать заново.

    py remote.py            бот стартует сразу
    py remote.py --idle     пульт поднимается, бот ждёт команды

Страница защищена токеном: без него ничего не работает. Токен берётся
из config.json (remote.token), а если его там нет — создаётся при первом
запуске и печатается в консоль.
"""

import gzip
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import core
import machine
import party
import stats
from core import log

HERE = core.HERE
SHOT = core.file("remote_shot.jpg")

cfg = core.load_config()
RCFG = cfg.get("remote", {})
PORT = RCFG.get("port", 8731)
SHOT_WIDTH = RCFG.get("shot_width", 1280)
SHOT_QUALITY = RCFG.get("shot_quality", 70)
TAIL_LINES = RCFG.get("log_lines", 25)


def ensure_token():
    token = RCFG.get("token")
    if token:
        return token
    token = secrets.token_urlsafe(12)
    path = core.file("local.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = {"_": "Личные настройки: пароли, свой домен, игровой id. "
                    "В репозиторий не уезжают."}
    raw.setdefault("remote", {})["token"] = token
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print("пароль пульта придуман и записан в local.json")
    return token


TOKEN = ensure_token()


class Bot:

    RAPID_S = 60
    MAX_RAPID = 5

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.started_at = None
        self.args = []
        self.want_running = False
        self.restarts = 0
        self.rapid = 0
        self.note = ""

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _spawn(self):
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--bot"]
        else:
            cmd = [sys.executable, "-X", "utf8",
                   os.path.join(core.CODE, "bot.py")]
        self.proc = subprocess.Popen(cmd + list(self.args), cwd=HERE)
        self.started_at = time.time()

    def start(self, args=()):
        with self.lock:
            if self.alive:
                return "уже работает"
            self.args = [a for a in args if not a.isdigit()]
            self.rapid = 0
            self.note = ""
            self.want_running = True
            self._spawn()
            return "запущен"

    def stop(self):
        with self.lock:
            self.want_running = False
            if not self.alive:
                return "и так не работает"
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            return "остановлен"

    def watch(self):
        while True:
            time.sleep(5)
            with self.lock:
                if not self.want_running or self.alive:
                    continue
                code = self.proc.poll() if self.proc else None
                lived = time.time() - (self.started_at or time.time())
                self.rapid = self.rapid + 1 if lived < self.RAPID_S else 0
                if self.rapid >= self.MAX_RAPID:
                    self.want_running = False
                    self.note = ("бот %d раз подряд падал сразу после старта "
                                 "— больше не поднимаю" % self.rapid)
                    log(self.note)
                    continue
                self.restarts += 1
                log("бот завершился сам (код %s, прожил %d с) — "
                    "поднимаю заново, перезапуск %d"
                    % (code, round(lived), self.restarts))
                self._spawn()

    def uptime(self):
        if not self.alive or not self.started_at:
            return ""
        s = int(time.time() - self.started_at)
        return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60)


bot = Bot()


class Tunnel:

    def __init__(self, mode):
        self.mode = mode
        self.proc = None
        self.url = None

    def where(self):
        want = (RCFG.get("cloudflared") or "").strip()
        if want and os.path.exists(want):
            return want
        found = shutil.which("cloudflared")
        if found:
            return found
        local = os.environ.get("LOCALAPPDATA", "")
        for guess in (os.path.join(local, "Microsoft", "WinGet", "Links",
                                   "cloudflared.exe"),
                      r"C:\Program Files\cloudflared\cloudflared.exe",
                      r"C:\Program Files (x86)\cloudflared\cloudflared.exe"):
            if guess and os.path.exists(guess):
                return guess
        return ""

    def start(self):
        if not self.mode:
            return
        exe = self.where()
        if not exe:
            print("   туннель не поднят: cloudflared не найден.")
            print("   Пульт работает в домашней сети. Нужен адрес снаружи —")
            print("   поставь: winget install Cloudflare.cloudflared")
            print("   Не нужен — выключи: remote.tunnel = null в настройках.")
            return
        if self.mode == "quick":
            cmd = [exe, "tunnel", "--url", "http://localhost:%d" % PORT]
        else:
            cmd = [exe, "tunnel", "run", "--url",
                   "http://localhost:%d" % PORT, self.mode]
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1)
        except OSError as e:
            print("   туннель не поднят (%s). Пульт работает в домашней "
                  "сети." % e)
            self.proc = None
            return
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        if not self.proc or not self.proc.stdout:
            return
        import re as _re
        pat = _re.compile(r"https://[a-z0-9-]+[.]trycloudflare[.]com")
        for line in self.proc.stdout:
            m = pat.search(line)
            if m and not self.url:
                self.url = m.group(0)
                print()
                offer("%s/?k=%s" % (self.url, TOKEN), "Адрес снаружи:")
                print()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


tunnel = Tunnel(RCFG.get("tunnel"))


def take_shot():
    img = core.grab([0, 0, core.SCREEN_W, core.SCREEN_H]).convert("RGB")
    if img.width > SHOT_WIDTH:
        h = round(img.height * SHOT_WIDTH / img.width)
        img = img.resize((SHOT_WIDTH, h))
    img.save(SHOT, "JPEG", quality=SHOT_QUALITY)
    return time.strftime("%H:%M:%S")


HOME_LOG_MAX = RCFG.get("home_log_kb", 400) * 1024


LOG_TAIL = RCFG.get("log_page_kb", 300) * 1024


def read_log(path=None, whole=False):
    path = path or core.LOG_FILE
    try:
        size = os.path.getsize(path)
        with open(path, encoding="utf-8", errors="replace") as f:
            if not whole and size > LOG_TAIL:
                f.seek(size - LOG_TAIL)
                f.readline()
                head = ("… показаны последние %d кб из %d. "
                        % (LOG_TAIL // 1024, size // 1024))
                return head + "\n\n" + f.read()
            return f.read()
    except Exception:
        return "лога пока нет"


def home_log():
    try:
        size = os.path.getsize(core.LOG_FILE)
        start = max(0, size - HOME_LOG_MAX) if HOME_LOG_MAX else 0
        with open(core.LOG_FILE, "rb") as f:
            f.seek(start)
            raw = f.read()
    except Exception:
        return "лога пока нет", 0
    pos = start + len(raw)
    if start:
        raw = raw[raw.find(b"\n") + 1:]
        head = "... начало лога тут не показано, целиком — кнопка «Логи» ...\n\n"
    else:
        head = ""
    return head + raw.decode("utf-8", "replace").replace("\r", ""), pos


def log_since(pos):
    try:
        size = os.path.getsize(core.LOG_FILE)
    except Exception:
        return pos, ""
    if pos > size:
        pos = 0
    if pos == size:
        return pos, ""
    with open(core.LOG_FILE, "rb") as f:
        f.seek(pos)
        raw = f.read()
    cut = raw.rfind(b"\n")
    if cut < 0:
        return pos, ""
    raw = raw[:cut + 1]
    return pos + len(raw), raw.decode("utf-8", "replace").replace("\r", "")


def shot_stamp():
    try:
        return int(os.path.getmtime(SHOT))
    except Exception:
        return 0


def log_choices(active):
    files = stats.log_files()
    if len(files) < 2:
        return ""
    out = []
    for p in files:
        name = os.path.basename(p)
        mark = " class=\"cur\"" if name == active else ""
        out.append('<a href="/log?k=%s&f=%s"%s>%s</a>'
                   % (TOKEN, urllib.parse.quote(name), mark, html.escape(name)))
    return '<div class="files">%s</div>' % " ".join(out)


FUNC_LABEL = [
    ("talents",   "Таланты"),
    ("abilities", "Способности"),
    ("items",     "Основной закуп"),
    ("late",      "Докуп"),
    ("books",     "Книги на остаток"),
    ("bkb",       "BKB каждую волну"),
    ("ult",       "Ульта каждую волну"),
    ("refresh",   "Рефрешер после ульты"),
    ("satanic",   "Сатаник на низком HP"),
    ("disarm",    "Ответ на бессилие"),
    ("death",     "F1 после смерти"),
    ("wave_start", "F1 в начале волны"),
    ("moon",      "Мун-предметы сразу"),
    ("boss_swap", "Предмет на боссе"),
    ("boss_attack", "Связка на боссе"),
    ("wave_move", "Связка на волне"),
    ("spam",      "Способность по кулдауну"),
]


def cur_hero():
    return conf_view().get("hero", "")


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def pick_hero(name):
    if name not in core.hero_files():
        return False
    if put_local("hero", name):
        log("билд переключён на %s — со следующего матча" % name)
    return True


MODES = [
    ("solo", "Один герой", "всё время тот, что выбран ниже"),
    ("sweep", "Обход всех", "каждый матч следующий, кто ещё не прошёл "
                            "выбранную сложность"),
]


def set_mode(name):
    if name not in {m[0] for m in MODES}:
        return False
    if put_local("mode", name):
        log("режим переключён на %s — со следующего матча" % name)
    return True


def sweep_html(conf):
    diff = str(conf.get("difficulty", ""))
    tried = core.progress().get(diff) or {}
    screen = core.load_config().get("screen_file") or ""
    ready = []
    for name in core.hero_files():
        h = core.read_json(core.hero_path(name))
        at = h.get("pick_at")
        at = at.get(screen) if isinstance(at, dict) else at
        if at and any(at):
            ready.append((name, h.get("gsi_name") or ""))
    ok = sum(1 for v in tried.values() if v.get("passed"))
    queue = []
    for i, (nm, g) in enumerate(ready):
        rec = tried.get(g) or {}
        if rec.get("passed"):
            continue
        queue.append((rec.get("tries", 0), i, nm))
    queue.sort()
    nextup = queue[0][2] if queue else ""
    rows = []
    for nm, g in sorted(ready):
        rec = tried.get(g)
        if not rec:
            cls, what, btn = "", "в очереди", ""
        else:
            good = rec.get("passed", True)
            cls = "ok" if good else "bad"
            what = ("%s, волн %d, попыток %d, %s"
                    % ("прошёл" if good else "не прошёл",
                       rec.get("waves", 0), rec.get("tries", 1),
                       rec.get("at", "")))
            btn = ('<button class="del" onclick="retry(this,\'%s\')">'
                   'сейчас</button>' % html.escape(g, quote=True))
        rows.append("<tr class='%s'><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % (cls, html.escape(nm), html.escape(what), btn))
    total = len(core.hero_files())
    return (
        "<div class='state'>Сложность %s: прошли <b>%d</b> из %d, в "
        "очереди %d.<br>Непрошедший пропускается до конца круга и "
        "пробуется снова следующим; «сейчас» двигает его в начало."
        "<br>Билдов всего %d, без снятой точки пика %d.<br>%s</div>%s"
        % (html.escape(diff), ok, len(ready), len(queue), total,
           total - len(ready),
           ("Следующий: <b>%s</b>" % html.escape(nextup or "—")),
           ("<table><tr><th>билд</th><th>сложность %s</th><th></th></tr>%s"
            "</table>" % (html.escape(diff), "".join(rows))) if rows else ""))


def difficulties():
    return core.load_config().get("difficulties") or []


def set_difficulty(n):
    lst = difficulties()
    if not 1 <= n <= len(lst):
        return False
    if put_local("difficulty", n):
        log("сложность переключена на %d (%s) — со следующего матча"
            % (n, lst[n - 1].get("name", "?")))
    return True


def set_func(name, key, value):
    path = core.hero_path(name or cur_hero())
    data = core.read_json(path)
    if not data:
        return False
    data.setdefault("functions", {})[key] = bool(value)
    write_json(path, data)
    return True



SKIP_FIELDS = ("functions", "items")


def walk_fields(obj, prefix=""):
    out = []
    for k, v in obj.items():
        if k.startswith("_") or k in SKIP_FIELDS:
            continue
        path = "%s.%s" % (prefix, k) if prefix else k
        note = obj.get("_" + k) or ""
        if isinstance(v, dict):
            out.append((path, None, v.get("_") or note))
            out += walk_fields(v, path)
        else:
            out.append((path, v, note))
    return out



SLOT_NAME = {"ult": "Ульта", "slot1": "Первая способность",
             "slot2": "Вторая способность", "slot3": "Третья способность",
             "slot4": "Четвёртая способность"}

FIELD_TEXT = {
    "title": ("Название", "Как билд подписан на пульте и в логе."),
    "gsi_name": ("Герой в игре",
                 "Имя героя в GSI: по нему бот проверяет, что пик не "
                 "промахнулся."),
    "pick_derived": ("Посчитанные точки пика",
                     "Профили экрана, где точка не снята руками, а "
                     "пересчитана по другой. Сними её — и строка уйдёт."),

    "talents": ("Таланты", "План по порядку, по записи на каждый ранг. "
                           "Руками его править незачем — есть сетка."),
    "talents.plan": ("План талантов",
                     "[дерево, ряд, колонка] на каждый ранг. Удобнее "
                     "кнопкой «Разложить таланты по сетке» выше."),

    "abilities": ("Способности",
                  "Что качать и до какого уровня. Клавиши общие для всех "
                  "билдов, они в «Общих настройках»."),
    "abilities.max_level": ("До какого уровня качать",
                            "Бот жмёт клавишу, пока способность не "
                            "дорастёт до этого числа. 0 — не качать."),
    "abilities.autocast": ("Автокаст",
                           "Один раз за матч, как только способность "
                           "выучена, бот включает ей автокаст."),
    "autocast.slot": ("Какой способности",
                      "ult, slot1, slot2 или slot3. Пусто — не включать."),
    "autocast.combo": ("Чем включить",
                       "Клавиши списком, например [\"ctrl\",\"alt\",\"q\"]."),
    "abilities.after_max": ("Когда качать нечего",
                            "Что нажимать, когда все способности в "
                            "максимуме."),

    "reactions": ("Реакции",
                  "Что бот делает в ответ на то, что видит в матче."),
    "reactions.wave_start": ("В начале волны",
                            "Самое первое на новой волне: F1, чтобы "
                            "герой был выделен и способности ушли "
                            "ему."),
    "wave_start.times": ("Сколько раз нажать",
                         "Первый F1 выделяет героя, второй наводит "
                         "на него камеру."),
    "wave_start.gap_s": ("Пауза между нажатиями", "Секунды."),
    "wave_start.min_gap_s": ("Не чаще, чем раз в",
                             "Секунды. Волну видят два дозора, "
                             "второй раз жать незачем."),
    "wave_start.lock_wait_s": ("Ждать очередь ввода",
                               "Дольше ждать смысла нет — волна уже "
                               "идёт."),
    "reactions.boss_attack": ("Связка на боссе",
                             "Правой кнопкой в середину экрана: герой "
                             "идёт к боссу и дальше бьёт его сам. Если "
                             "в билде задана клавиша, она жмётся перед "
                             "кликом. Общая для всех билдов."),
    "boss_attack.button": ("Какой кнопкой",
                           "right — правой (атаковать цель), left — "
                           "левой."),
    "boss_attack.click": ("Куда кликнуть",
                          "center — середина экрана, или [x, y]."),
    "boss_attack.after_s": ("Ждать секунд",
                            "Пауза после появления босса."),
    "boss_attack.key_wait_s": ("Пауза после клавиши", "Секунды."),
    "boss_attack.lock_wait_s": ("Ждать очередь ввода", "Секунды."),
    "reactions.death": ("Смерть героя",
                        "Что нажать, когда герой умер. Обычно F1 — "
                        "возродиться."),
    "reactions.disarm": ("Бессилие",
                         "Ответ на обезоруживание: обычно способность, "
                         "которая его снимает."),
    "disarm.after_s": ("Ждать секунд",
                       "Сколько терпеть бессилие, прежде чем нажимать."),
    "reactions.satanic": ("Сатаник", "Когда жать сатаник на низком "
                                     "здоровье."),
    "satanic.hp_percent": ("Здоровье, %", "Ниже этого — нажать."),
    "satanic.item": ("Предмет", "Что жать. Нажатие засчитывается по уходу "
                                "предмета в откат."),

    "reactions.ult": ("Ульта на волне", "Когда и как бот жмёт ульту."),
    "ult.ability": ("Какая способность", "ult, slot1, slot2 или slot3."),
    "ult.after_s": ("Ждать секунд", "Пауза после появления волны."),
    "ult.fast": ("Быстрый дозор",
                 "Жать сразу по приходу пакета GSI, не дожидаясь своей "
                 "секунды."),
    "ult.fast_s": ("Окно быстрого дозора",
                   "Сколько секунд после начала волны ловим момент."),
    "ult.by_bar": ("Смотреть на полосу волны",
                   "Ловить начало волны ещё и по полоске на экране, не "
                   "только по GSI."),
    "ult.on_boss": ("На боссе тоже", "Жать ульту и на волне босса."),
    "ult.min_gap_s": ("Не чаще, чем раз в",
                      "Защита от двойного нажатия, секунды."),
    "ult.max_late_s": ("Предел опоздания",
                       "Волна идёт дольше этого — уже не жать."),
    "ult.lock_wait_s": ("Ждать очередь ввода",
                        "Сколько ждать, пока освободятся мышь и "
                        "клавиатура."),
    "ult.refresh": ("Рефрешер",
                    "Сбросить откат ульты и ударить второй раз."),
    "refresh.item": ("Предметы", "Что считать рефрешером."),
    "refresh.slot": ("Слот", "Из какого слота жать. Пусто — найти по "
                             "имени."),
    "refresh.gap_s": ("Пауза перед ультой",
                      "Сколько ждать после рефрешера."),
    "refresh.repeat_after_s": ("Повтор не раньше",
                               "Пауза до следующего рефрешера."),
    "ult.after_cast": ("После ульты", "Что сделать сразу после каста."),
    "after_cast.click": ("Куда кликнуть",
                         "center — середина экрана, или координата [x, y]."),
    "after_cast.key_wait_s": ("Пауза перед клавишей", "Секунды."),

    "launch.console_setup": ("Команды в консоль при старте матча",
                            "Одной строкой через точку с запятой. Нужны, "
                            "чтобы камера не цеплялась за героя и автоатака "
                            "не слетала. Пусто — ничего не отправлять."),
    "reactions.spam": ("Способность по кулдауну",
                       "Жать одну способность по кругу, пока идёт волна. "
                       "Выключено по умолчанию — включается выключателем "
                       "наверху страницы."),
    "spam.ability": ("Какая способность", "ult, slot1, slot2 или slot3."),
    "spam.after_wave_s": ("Первый раз через",
                          "Секунд после выхода крипов. Начало волны — это "
                          "выход мобов."),
    "spam.cooldown_s": ("Откат способности",
                        "Секунды. Откатов эта кастомка в GSI не присылает, "
                        "поэтому бот считает по этому числу: ставь с "
                        "небольшим запасом."),
    "spam.times": ("Сколько раз за волну",
                   "0 — пока волна не кончится."),
    "spam.on_boss": ("На боссе тоже", "Жать и на волне босса."),
    "spam.key": ("Клавиша",
                 "Пусто — взять из настроек способностей по выбранному "
                 "слоту."),
    "spam.click": ("Куда кликнуть",
                   "Пусто — никуда, center — середина экрана, или [x, y]. "
                   "Нужно способностям, которые бьют по точке."),
    "spam.button": ("Какой кнопкой", "left — левой, right — правой."),
    "spam.key_wait_s": ("Пауза после клавиши", "Секунды."),
    "spam.lock_wait_s": ("Ждать очередь ввода",
                         "Сколько ждать, пока освободятся мышь и "
                         "клавиатура."),

    "reactions.wave_move": ("Связка на волне",
                            "Добежать и применить способность на заданной "
                            "волне заданной арены."),
    "wave_move.unit": ("Крип волны",
                       "Имена из GSI: по ним бот узнаёт нужную волну."),
    "wave_move.prev": ("Волны до неё",
                       "Чем подтверждается, что арена та самая: имена "
                       "предыдущих волн по порядку."),
    "wave_move.move": ("Куда бежать", "Точка правой кнопки."),
    "wave_move.move_wait_s": ("Сколько бежать",
                              "Пауза, пока герой добегает, секунды."),
    "wave_move.key": ("Клавиша способности", "Что нажать, добежав."),
    "wave_move.key_wait_s": ("Пауза после клавиши", "Секунды."),
    "wave_move.cast": ("Куда применить", "Точка левой кнопки."),
    "wave_move.after_s": ("Ждать секунд", "Пауза после начала волны."),
    "wave_move.lock_wait_s": ("Ждать очередь ввода",
                              "Сколько ждать свободных мыши и клавиатуры."),

    "key": ("Клавиши",
            "Списком: [\"f\"] — одна, [\"ctrl\",\"q\"] — вместе."),
}


def ability_keys():
    conf = core.read_json(core.file("config.json")) or {}
    return ((conf.get("abilities") or {}).get("keys") or {})


def field_text(path, note=""):
    parts = path.split(".")
    if len(parts) >= 2 and parts[-2] == "max_level" and parts[-1] in SLOT_NAME:
        keys = ability_keys().get(parts[-1]) or []
        hint = ("клавиша " + " + ".join(keys)) if keys else ""
        return SLOT_NAME[parts[-1]], (note or hint)
    for k in (path, ".".join(parts[-2:]), parts[-1]):
        if k in FIELD_TEXT:
            label, hint = FIELD_TEXT[k]
            return label, (note or hint)
    return parts[-1], note


def field_html(path, val, note):
    name, note = field_text(path, note)
    label = html.escape(name)
    hint = ('<div class="dim">%s</div>' % html.escape(note)) if note else ""
    pid = html.escape(path, quote=True)
    if path == "gsi_name":
        body = '<select data-p="%s">%s</select>' % (pid, hero_options(val))
    elif isinstance(val, bool):
        body = ('<select data-p="%s"><option%s>да</option>'
                '<option%s>нет</option></select>'
                % (pid, " selected" if val else "",
                   "" if val else " selected"))
    elif isinstance(val, (int, float, str)):
        body = ('<input data-p="%s" value="%s">'
                % (pid, html.escape(str(val), quote=True)))
    else:
        text = json.dumps(val, ensure_ascii=False)
        rows = min(14, max(2, len(text) // 60 + 1))
        body = ('<textarea data-p="%s" rows="%d">%s</textarea>'
                % (pid, rows, html.escape(text)))
    return '<div class="fld"><label>%s</label>%s%s</div>' % (label, body, hint)


def apply_field(data, path, raw):
    keys = path.split(".")
    node = data
    for k in keys[:-1]:
        if not isinstance(node.get(k), dict):
            raise ValueError("нет раздела %s" % k)
        node = node[k]
    last = keys[-1]
    if last not in node:
        raise ValueError("нет такого поля")
    old = node[last]
    if isinstance(old, bool):
        node[last] = str(raw).strip().lower() in ("да", "true", "1", "вкл")
    elif isinstance(old, int):
        node[last] = int(float(str(raw).strip().replace(",", ".")))
    elif isinstance(old, float):
        node[last] = float(str(raw).strip().replace(",", "."))
    elif isinstance(old, str):
        node[last] = str(raw)
    else:
        node[last] = json.loads(raw)


def save_hero(name, fields):
    if name not in core.hero_files():
        return "нет такого билда"
    data = core.read_json(core.hero_path(name))
    if not data:
        return "билд не прочитан"
    copy = json.loads(json.dumps(data))
    for p, raw in fields.items():
        try:
            apply_field(copy, p, raw)
        except Exception as e:
            return "%s — %s" % (p, e)
    write_json(core.hero_path(name), copy)
    log("билд %s изменён с пульта, полей: %d" % (name, len(fields)))
    return ""


class Task:

    def __init__(self):
        self.busy = False
        self.text = ""
        self.lock = threading.Lock()

    def run(self, name, fn):
        with self.lock:
            if self.busy:
                return False, self.text
            self.busy = True
            self.text = name + "…"

        def work():
            try:
                self.text = fn() or "готово"
            except Exception as e:
                self.text = "сбой: %s" % e
                log("%s: %s" % (name, e))
            finally:
                self.busy = False

        threading.Thread(target=work, daemon=True).start()
        return True, self.text


task = Task()

OFF_CODE = {"code": "", "until": 0}
OFF_WINDOW = 20


def arm_poweroff():
    OFF_CODE["code"] = secrets.token_urlsafe(8)
    OFF_CODE["until"] = time.time() + OFF_WINDOW
    return OFF_CODE["code"]


def do_poweroff(code):
    if not OFF_CODE["code"] or time.time() > OFF_CODE["until"]:
        return "подтверждение просрочено, нажми ещё раз"
    if code != OFF_CODE["code"]:
        return "подтверждение не совпало"
    OFF_CODE["code"] = ""
    log("выключение компьютера с пульта")
    bot.stop()
    return machine.shutdown()


def workshop_id():
    cmd = (core.read_json(core.file("config.json"))
           .get("launch", {}).get("command", ""))
    m = re.search(r"\d{6,}", cmd)
    return m.group(0) if m else ""


def game_html():
    d = machine.dota_state()
    if d.get("ok"):
        if d["updating"]:
            dline = "обновляется прямо сейчас"
        elif d["need_update"]:
            dline = "<b>ждёт обновления</b> — поставится при перезапуске"
        else:
            dline = "обновлений нет"
        dota = ("Dota: сборка %s, обновлена %s<br>%s, сейчас %s"
                % (html.escape(d["build"]), html.escape(d["updated"]), dline,
                   "запущена" if machine.dota_running() else "<b>не запущена</b>"))
    else:
        dota = "Dota: %s" % html.escape(d.get("why", "?"))

    wid = workshop_id()
    a = machine.addon_state(wid) if wid else {"ok": False, "why": "id не найден"}
    if a.get("ok"):
        if a["fresh"]:
            aline = "актуальна"
        else:
            aline = ("<b>вышло обновление</b> (%s) — подтянется, когда Dota "
                     "снова запросит кастомку"
                     % html.escape(a["latest_updated"] or "?"))
        addon = ("Кастомка %s: скачана %s<br>%s"
                 % (html.escape(a["id"]), html.escape(a["updated"]), aline))
    else:
        addon = "Кастомка: %s" % html.escape(a.get("why", "?"))

    return (
        "<div class='state'>%s<br><br>%s</div>"
        "<button class='wide' onclick=\"act2('/dotarestart','Перезапускаю Dota')\">"
        "Перезапустить Dota<span class='dim'> · закрыть и открыть заново через "
        "Steam</span></button>"
        "<button class='wide' onclick=\"act2('/addoncheck','Проверяю кастомку')\">"
        "Проверить кастомку<span class='dim'> · и перезапустить Dota, если "
        "вышло обновление</span></button>"
        "<button class='wide stop' id='off' onclick='poweroff()'>"
        "Выключить компьютер<span class='dim' id='offhint'> · нажми, потом "
        "подтверди</span></button>"
        "<div id='task' class='dim'></div>" % (dota, addon))


def hero_options(now):
    out = []
    known = False
    for h in core.gamedata().get("heroes", []):
        sel = ""
        if h["name"] == now:
            sel, known = " selected", True
        out.append('<option value="%s"%s>%s</option>'
                   % (html.escape(h["name"], quote=True), sel,
                      html.escape(h["title"])))
    if now and not known:
        out.insert(0, '<option value="%s" selected>%s — нет в справочнике'
                      '</option>' % (html.escape(now, quote=True),
                                     html.escape(now)))
    return "".join(out)


def items_editor(hero):
    rows = []
    for e in hero.get("items") or []:
        row = {k: v for k, v in e.items()
               if k not in ("name", "role", "cost")}
        row.update({"name": e.get("name", ""),
                    "role": e.get("role", ""),
                    "cost": e.get("cost") or 0})
        rows.append(row)
    conf = core.read_json(core.file("config.json"))
    data = {
        "rows": rows,
        "all": [{"n": i["name"], "t": i["title"], "c": i["cost"]}
                for i in core.gamedata().get("items", [])],
        "learned": {k: v.get("cost") for k, v in core.prices().items()},
        "roles": [{"k": k, "t": (v.get("title") or k),
                   "s": v.get("slot")}
                  for k, v in (conf.get("roles") or {}).items()
                  if isinstance(v, dict)],
    }
    return ('<div id="itemed"></div>'
            '<div class="row"><button onclick="addRow()">Добавить</button></div>'
            '<textarea data-p="items" id="itemsjson" class="hid"></textarea>'
            '<script>var ITEMDATA=%s;</script>'
            % json.dumps(data, ensure_ascii=False))


def roles_html():
    conf = core.read_json(core.file("config.json"))
    roles = conf.get("roles") or {}
    if not roles:
        return "<p>В config.json нет раздела roles.</p>"
    out = ["<div class='state'>Функции одинаковы для всех героев. В билде "
           "у предмета указывается только роль и место в списке, а что она "
           "делает и чем жмёт — здесь.</div>"]
    for key, v in roles.items():
        out.append("<h3>%s<span class='dim'> · %s</span></h3>"
                   % (html.escape(v.get("title") or key), html.escape(key)))
        note = v.get("_")
        if note:
            out.append("<div class='dim' style='margin:-4px 0 8px'>%s</div>"
                       % html.escape(note))
        for k, val in v.items():
            if k.startswith("_") or k == "title":
                continue
            out.append(field_html("roles.%s.%s" % (key, k), val, ""))
    out.append("<div class='row'><button class='go' onclick='saveRoles()'>"
               "Сохранить</button></div><div id='saved' class='dim'></div>")
    return "".join(out)


def save_roles(fields):
    path = core.file("config.json")
    data = core.read_json(path)
    if not data:
        return "config.json не прочитан"
    copy = json.loads(json.dumps(data))
    for p, raw in fields.items():
        if not p.startswith("roles."):
            return "чужое поле: %s" % p
        try:
            apply_field(copy, p, raw)
        except Exception as e:
            return "%s — %s" % (p, e)
    write_json(path, copy)
    log("функции закупа изменены с пульта, полей: %d" % len(fields))
    return ""


COMMON_SKIP = ("hero", "mode", "sweep", "difficulty", "roles", "party",
               "screen_file", "hero_file")


def cfg_fields(obj, prefix=""):
    out = []
    for k, v in obj.items():
        if k.startswith("_"):
            continue
        path = "%s.%s" % (prefix, k) if prefix else k
        note = obj.get("_" + k) or ""
        if isinstance(v, dict):
            out.append(("sec", path, None, v.get("_") or note))
            out += cfg_fields(v, path)
        else:
            out.append(("fld", path, v, note))
    return out


def is_point(val):
    if val is None:
        return True
    return (isinstance(val, list) and len(val) == 2
            and all(isinstance(x, (int, float)) for x in val))


def fields_block(items, pick=False):
    out = []
    for kind, path, val, note in items:
        if kind == "sec":
            out.append("<h3>%s</h3>" % html.escape(path))
            if note:
                out.append("<div class='dim' style='margin:-4px 0 8px'>%s"
                           "</div>" % html.escape(note))
            continue
        out.append(field_html(path, val, note))
        if pick and is_point(val) and not path.endswith("resolution"):
            out.append(grab_button(path))
    return "".join(out)


SAVE_ROW = ("<div class='row'><button class='go' onclick='%s()'>Сохранить"
            "</button></div><div id='saved' class='dim'></div>")



LOCAL = core.file("local.json")

SECRET_PATHS = ("remote.token", "remote.hostname", "remote.cloudflared",
                "party.token", "party.invite_id")


def conf_view():
    return core.merge(core.read_json(core.file("config.json")) or {},
                      core.read_json(LOCAL) or {})


def dig(data, path):
    for k in path.split("."):
        data = data.get(k) if isinstance(data, dict) else None
    return data


def put_field(target, path, raw, old):
    if isinstance(old, bool):
        val = str(raw).strip().lower() in ("да", "true", "1", "вкл")
    elif isinstance(old, int) and not isinstance(old, bool):
        val = int(float(str(raw).strip().replace(",", ".")))
    elif isinstance(old, float):
        val = float(str(raw).strip().replace(",", "."))
    elif isinstance(old, str):
        val = str(raw)
    elif old is None:
        text = str(raw).strip()
        if text in ("", "null", "None"):
            val = None
        else:
            try:
                val = json.loads(text)
            except ValueError:
                val = text
    else:
        val = json.loads(raw)
    set_path(target, path, val)


def put_local(path, value):
    if dig(conf_view(), path) == value:
        return False
    data = core.read_json(LOCAL) or {}
    set_path(data, path, value)
    write_json(LOCAL, data)
    return True


def drop_path(data, path):
    keys = path.split(".")
    nodes = [data]
    for k in keys[:-1]:
        nxt = nodes[-1].get(k)
        if not isinstance(nxt, dict):
            return False
        nodes.append(nxt)
    if keys[-1] not in nodes[-1]:
        return False
    nodes[-1].pop(keys[-1])
    for node, key in zip(reversed(nodes[:-1]), reversed(keys[:-1])):
        if node.get(key) == {}:
            node.pop(key)
    return True


def save_split(fields, guard=None):
    conf = core.read_json(core.file("config.json")) or {}
    local = core.read_json(LOCAL) or {}
    view = core.merge(conf, local)
    copy = json.loads(json.dumps(local))
    for p, raw in fields.items():
        if guard:
            err = guard(p)
            if err:
                return err
        try:
            put_field(copy, p, raw, dig(view, p))
        except Exception as e:
            return "%s — %s" % (p, e)
        if p not in SECRET_PATHS and dig(copy, p) == dig(conf, p):
            drop_path(copy, p)
    write_json(LOCAL, copy)
    return ""


def common_html():
    conf = conf_view()
    if not conf:
        return "<p>config.json не прочитан.</p>"
    out = ["<div class='state'>Механика, одинаковая для всех героев и "
           "экранов: тайминги, пороги, магазин, наблюдатель, пульт. "
           "Координаты лежат отдельно, в «Координатах экрана», а что "
           "качать и покупать — в настройках героя.<br><br>"
           "Бот перечитывает настройки между матчами: начатый "
           "доигрывается как был.</div>"]
    plain = [(k, v, conf.get("_" + k) or "") for k, v in conf.items()
             if not k.startswith("_") and k not in COMMON_SKIP
             and not isinstance(v, dict)]
    if plain:
        out.append("<h3>общее</h3>")
        out += [field_html(k, v, n) for k, v, n in plain]
    for k, v in conf.items():
        if k.startswith("_") or k in COMMON_SKIP or not isinstance(v, dict):
            continue
        out.append(fields_block([("sec", k, None, v.get("_") or "")]
                                + cfg_fields(v, k)))
    out.append(SAVE_ROW % "saveCommon")
    return "".join(out)


def save_common(fields):
    def guard(p):
        head = p.split(".")[0]
        if head in COMMON_SKIP:
            return "раздел %s правится на своей вкладке" % head
        return ""

    err = save_split(fields, guard)
    if not err:
        log("общие настройки изменены с пульта, полей: %d" % len(fields))
    return err


def screen_profile():
    return core.pick_screen(conf_view().get("screen"))




def point_delay():
    return float(RCFG.get("point_delay_s", 5))


def screen_size(name):
    try:
        w, h = name.lower().split("x")
        return int(w), int(h)
    except Exception:
        return 0, 0


def set_path(data, path, value):
    node, keys = data, path.split(".")
    for k in keys[:-1]:
        if not isinstance(node.get(k), dict):
            node[k] = {}
        node = node[k]
    node[keys[-1]] = value


def take_point(path, hero=""):
    if hero and not path.startswith("pick_at"):
        return {}, "у билда снимается только точка пика"
    screens = [s for s in core.screen_files() if screen_size(s)[0]]
    if not screens:
        return {}, "нет ни одного профиля экрана"
    time.sleep(point_delay())
    x, y = core.cursor_pos()
    bw, bh = core.SCREEN_W, core.SCREEN_H
    here = "%dx%d" % (bw, bh)
    out = {}
    if hero:
        if hero not in core.hero_files():
            return {}, "нет такого билда"
        data = core.read_json(core.hero_path(hero))
        if not data:
            return {}, "билд не читается"
        for scr in screens:
            w, h = screen_size(scr)
            out[scr] = [round(x * w / bw), round(y * h / bh)]
            set_path(data, "pick_at." + scr, out[scr])
        other = [s for s in out if s != here]
        if other:
            data["pick_derived"] = other
        else:
            data.pop("pick_derived", None)
        write_json(core.hero_path(hero), data)
        log("точка пика билда %s снята с курсора: %s"
            % (hero, ", ".join("%s %s" % (s, out[s]) for s in out)))
        return out, ""
    for scr in screens:
        data = core.read_json(core.screen_path(scr))
        if not data:
            continue
        w, h = screen_size(scr)
        out[scr] = [round(x * w / bw), round(y * h / bh)]
        set_path(data, path, out[scr])
        write_json(core.screen_path(scr), data)
    if not out:
        return {}, "профили экрана не прочитались"
    log("точка %s снята с курсора: %s"
        % (path, ", ".join("%s %s" % (s, out[s]) for s in out)))
    return out, ""


def grab_button(path, hero=""):
    return ('<button class="pick" onclick="grabPoint(this,&quot;%s&quot;,'
            '&quot;%s&quot;)">Снять координату \u00b7 %d с и курсор'
            '</button>'
            % (html.escape(path, quote=True), html.escape(hero, quote=True),
               int(point_delay())))


POINT_JS = """
// Снятие точки: жмём на телефоне, наводим мышь на компьютере, через
// отсчёт бот сам подсматривает курсор и пишет точку во все профили.
function grabPoint(b, path, hero){
 var note=document.getElementById('grab'); if(!note) return;
 var left=%(d)d, old=b.textContent;
 b.disabled=true; b.textContent='наведи мышь\u2026 '+left;
 note.style.color=''; note.textContent='жду курсор';
 var t=setInterval(function(){
  left--;
  b.textContent = left>0 ? ('наведи мышь\u2026 '+left) : 'снимаю\u2026';
  if(left<=0) clearInterval(t);
 },1000);
 fetch('/takepoint?k='+K+'&path='+encodeURIComponent(path)+
       '&hero='+encodeURIComponent(hero||''),{cache:'no-store'})
  .then(function(r){return r.json();})
  .then(function(d){
   clearInterval(t); b.disabled=false; b.textContent=old;
   if(!d.ok){ note.style.color='#ff8b8b';
    note.textContent='не вышло \u2014 '+d.err; return; }
   var txt=[];
   for(var s in d.at){
    txt.push(s+' \u2192 ['+d.at[s][0]+', '+d.at[s][1]+']');
    var key = hero ? ('pick_at.'+s) : (s==d.cur ? path : null);
    if(key){
     var el=document.querySelector('[data-p="'+key+'"]');
     if(el) el.value='['+d.at[s][0]+', '+d.at[s][1]+']';
    }
   }
   note.style.color='#6ee7a0';
   note.textContent='записано: '+txt.join(', ');
  })
  .catch(function(){ clearInterval(t); b.disabled=false; b.textContent=old;
   note.style.color='#ff8b8b'; note.textContent='нет связи'; });
}
function saveCoords(){ postFields('/savecoords?k=%(k)s'); }
"""


def coords_html():
    scr = screen_profile()
    if not scr:
        return "<p>Не выбран профиль экрана (config.json, screen).</p>"
    data = core.read_json(core.screen_path(scr))
    if not data:
        return "<p>Профиль screens/%s.json не прочитан.</p>" % html.escape(scr)
    empty = [p for kind, p, v, _ in cfg_fields(data)
             if kind == "fld" and (v is None or not any(v)
                                   if isinstance(v, list) else v is None)]
    head = ("<div class='state'>Профиль <b>%s</b>, экран сейчас %dx%d.<br><br>"
            "<b>Снять координату</b>: нажми у нужного поля, наведи мышь на "
            "точку в игре и жди %d секунд. Бот подсмотрит курсор, "
            "пересчитает точку во все профили экрана (%s) и запишет сразу "
            "— «Сохранить» для этого не нужно.%s</div>"
            % (html.escape(scr), core.SCREEN_W, core.SCREEN_H,
               int(point_delay()),
               html.escape(", ".join(core.screen_files())),
               ("<br><br>Не сняты: <b>%s</b>" % html.escape(", ".join(empty)))
               if empty else ""))
    body = fields_block(cfg_fields(data), pick=True)
    return (head + "<div id='grab' class='dim'></div>" + body
            + (SAVE_ROW % "saveCoords"))


def save_coords(fields):
    scr = screen_profile()
    if not scr:
        return "не выбран профиль экрана"
    path = core.screen_path(scr)
    data = core.read_json(path)
    if not data:
        return "профиль %s не прочитан" % scr
    copy = json.loads(json.dumps(data))
    for p, raw in fields.items():
        try:
            apply_field(copy, p, raw)
        except Exception as e:
            return "%s — %s" % (p, e)
    write_json(path, copy)
    log("координаты экрана %s изменены с пульта, полей: %d"
        % (scr, len(fields)))
    return ""



ROLE_NAMES = {"main": "Мейн", "second": "Второй"}


def party_conf():
    return conf_view().get("party") or {}


def set_party(on):
    if put_local("party.on", bool(on)):
        log("пати %s с пульта — со следующего матча"
            % ("включена" if on else "выключена"))
    return True


def set_prole(role):
    if role not in ROLE_NAMES:
        return False
    if put_local("party.role", role):
        log("роль в пати: %s — применится после перезапуска бота" % role)
    return True


def party_state_html():
    st = party.read_state()
    if not st:
        return ("<div class='state'>Бот ещё ничего не рассказывал про пати: "
                "он пишет это, пока запущен с включённым режимом.</div>")
    try:
        age = time.time() - os.path.getmtime(party.STATE_FILE)
    except OSError:
        age = 0
    peer = st.get("peer") or {}
    rows = [
        ("этот ПК", "%s, роль %s, билд %s"
         % (st.get("name") or "?", ROLE_NAMES.get(st.get("role"), "?"),
            st.get("hero") or "?")),
        ("этап", st.get("stage") or "—"),
        ("напарник", "%s, роль %s, билд %s, этап %s"
         % (peer.get("name") or "?", ROLE_NAMES.get(peer.get("role"), "?"),
            peer.get("hero") or "?", peer.get("stage") or "—")
         if peer else "не слышно"),
        ("связь", ("есть" if st.get("alive") else "нет")
         + ("" if st.get("peer_age") is None
            else ", пакет %s с назад" % st["peer_age"])),
        ("вместе", "да" if st.get("together") else "нет"),
    ]
    body = "".join("<tr><td>%s</td><td>%s</td></tr>"
                   % (html.escape(k), html.escape(str(v))) for k, v in rows)
    return ("<div class='state'>Данные от %s%s</div><table>%s</table>"
            % (html.escape(st.get("at") or "?"),
               ", бот уже не пишет — похоже, остановлен" if age > 60 else "",
               body))


def party_btn(now, call, title, note):
    return ('<button class="wide%s" onclick="%s">'
            '<span class="dot%s"></span>%s<span class="dim"> · %s</span>'
            '</button>' % (" go" if now else "", call, " on" if now else "",
                           html.escape(title), html.escape(note)))


def party_html():
    conf = party_conf()
    if not conf:
        return "<p>В config.json нет раздела party.</p>"
    on = bool(conf.get("on"))
    role = conf.get("role") or "main"
    return "".join([
        "<div class='state'>Вдвоём играют два одинаковых бота на двух ПК в "
        "одной сети. Мейн зовёт напарника и жмёт поиск, второй принимает "
        "приглашение и идёт за ним; билд, таланты и закуп у каждого свои, "
        "по его собственным настройкам.<br><br>"
        "Пароль пати должен совпадать на двух ПК. Пульты при этом "
        "остаются разными: у каждого ПК свой пароль на вход сюда.<br><br>"
        "Связь ищется сама по сети, адрес напарника вписывать не надо. "
        "Брандмауэр Windows спросит про порт один раз — разреши для "
        "частной сети.</div>",
        "<h2>Как играем</h2>",
        party_btn(not on, "setParty(0)", "Соло", "один бот, как обычно"),
        party_btn(on, "setParty(1)", "Пати", "вдвоём со вторым ПК"),
        "<h2>Роль этого ПК</h2>",
        party_btn(role == "main", "setRole(&quot;main&quot;)", "Мейн",
                  "зовёт напарника и жмёт поиск"),
        party_btn(role == "second", "setRole(&quot;second&quot;)", "Второй",
                  "ждёт приглашения и принимает"),
        "<div class='dim'>Роль меняется с перезапуском бота: на ней "
        "держится уже открытая связь.</div>",
        "<h2>Связь</h2>", party_state_html(),
        "<h2>Настройки пати</h2>",
        "<div class='dim' style='margin:-4px 0 8px'>Точки «пригласить», "
        "поле ввода и «принять» снимаются на странице «Координаты "
        "экрана».</div>",
        fields_block(cfg_fields(conf, "party")),
        SAVE_ROW % "saveParty",
    ])


def save_party(fields):
    def guard(p):
        return ("" if p.startswith("party.")
                else "поле %s не из настроек пати" % p)

    err = save_split(fields, guard)
    if not err:
        log("настройки пати изменены с пульта, полей: %d" % len(fields))
    return err


PARTY_JS = """
function setParty(v){
 fetch('/setparty?k=%(k)s&on='+v,{cache:'no-store'})
  .then(function(){ location.reload(); });
}
function setRole(v){
 fetch('/setprole?k=%(k)s&role='+v,{cache:'no-store'})
  .then(function(){ location.reload(); });
}
function saveParty(){ postFields('/saveparty?k=%(k)s'); }
"""



TREE_NAME = {"1": "Сила", "2": "Ловкость", "3": "Интеллект"}


def talent_grid(gsi):
    d = core.read_json(core.file("talents_grid.json")) or {}
    return ((d.get("heroes") or {}).get(gsi or "") or {},
            d.get("row_need") or [])


def plan_flat(hero):
    out = []
    for e in ((hero.get("talents") or {}).get("plan") or []):
        if len(e) < 3:
            continue
        out.extend([[e[0], e[1], e[2]]] * (e[3] if len(e) > 3 else 1))
    return out


def talents_html(name):
    if name not in core.hero_files():
        return "<p>Нет такого билда.</p>"
    h = core.read_json(core.hero_path(name))
    cells, need = talent_grid(h.get("gsi_name"))
    title = html.escape(h.get("title") or name)
    if not cells:
        return ("<div class='state'>Билд <b>" + title + "</b>.<br><br>"
                "Для героя <b>" + html.escape(h.get("gsi_name") or "?")
                + "</b> в talents_grid.json нет правил, рисовать сетку не "
                "из чего. План можно править текстом на странице билда.</div>")
    data = {"cells": cells, "need": need, "plan": plan_flat(h),
            "trees": TREE_NAME}
    return ("".join([
        "<div class='state'>Билд <b>", title, "</b>. Жми ячейки — каждый "
        "клик добавляет ранг в конец плана. По ячейке с рангами жми ещё "
        "раз, чтобы добавить следующий; убрать — крестик на шаге.<br><br>"
        "Серые ряды ещё закрыты: аддон открывает ряд, когда в этом "
        "дереве набрано нужное число рангов.</div>",
        "<div id='warn'></div>",
        "<h2>Порядок кликов</h2>",
        "<div id='cnt' class='dim'></div>",
        "<div id='plan' class='chips'></div>",
        "<div class='row'><button onclick='undo()'>Убрать последний</button>"
        "<button onclick='wipe()'>Очистить</button></div>",
        "<div id='trees'></div>",
        "<textarea data-p='talents.plan' id='planjson' class='hid'>"
        "</textarea>",
        "<div class='row'><button class='go' onclick='saveHero()'>"
        "Сохранить план</button></div><div id='saved' class='dim'></div>",
        "<script>var PLAN=", json.dumps(data, ensure_ascii=False),
        ";</script>"]))


TALENTS_JS = """
var CELLS=PLAN.cells, NEED=PLAN.need, STEPS=PLAN.plan, BYMOD={};
for(var k in CELLS) BYMOD[CELLS[k].mod]=k;
function key(s){return s[0]+','+s[1]+','+s[2];}
function short(ic){var p=ic.split('/');
 return p.length>1?('\u2605'+p[1]):ic;}
// Та же проверка, что у бота в --check: первый шаг, который по правилам
// аддона взять нельзя. Дальше первой ошибки смотреть нечего — после неё
// расходится весь план.
function check(){
 var lvl={}, cnt={1:0,2:0,3:0}, bad=null;
 for(var i=0;i<STEPS.length;i++){
  var s=STEPS[i], k=key(s), v=CELLS[k];
  if(!v){bad=[i,'ячейка '+k+' пустая — там нет таланта'];break;}
  if((lvl[k]||0)>=v.max){bad=[i,'у «'+short(v.icon)+'» всего '+v.max+
    ' ранг(ов), а берём больше'];break;}
  if(cnt[s[0]]<NEED[s[1]-1]){bad=[i,'ряд '+s[1]+' в «'+PLAN.trees[s[0]]+
    '» открывается с '+NEED[s[1]-1]+' рангов, а есть '+cnt[s[0]]];break;}
  if(v.req){var hv=lvl[BYMOD[v.req[0]]]||0;
   if(hv!=v.req[1]){bad=[i,'«'+short(v.icon)+'» требует «'+v.req[0]+
     '» ровно '+v.req[1]+' ранга, а есть '+hv];break;}}
  lvl[k]=(lvl[k]||0)+1; cnt[s[0]]++;
 }
 return {lvl:lvl,cnt:cnt,bad:bad};
}
// Сколько рангов в ячейке и в дереве всего, без оглядки на правила:
// сетку надо рисовать целиком, даже если план где-то спорный.
function tally(){
 var lvl={}, cnt={1:0,2:0,3:0};
 for(var i=0;i<STEPS.length;i++){
  var k=key(STEPS[i]); lvl[k]=(lvl[k]||0)+1; cnt[STEPS[i][0]]++;
 }
 return {lvl:lvl,cnt:cnt};
}
// Выбранный шаг — точка вставки: клик по ячейке кладёт ранг сразу
// после него, а не в конец. Без этого забытый ранг в середине
// плана правился бы только переписыванием хвоста.
var at=null;
function add(t,r,c){
 if(at===null){ STEPS.push([t,r,c]); }
 else{ STEPS.splice(at+1,0,[t,r,c]); at++; }
 draw();
}
function pick(i){ at=(at===i?null:i); draw(); }
function del(i){
 STEPS.splice(i,1);
 if(at!==null){ if(at===i) at=null; else if(at>i) at--; }
 draw();
}
function undo(){ STEPS.pop(); at=null; draw(); }
function wipe(){ if(confirm('Стереть весь план?')){ STEPS=[]; at=null; draw(); } }
function draw(){
 var st=check(), all=tally();
 var w=document.getElementById('warn');
 if(st.bad){
  w.className='state bad';
  w.innerHTML='Шаг '+(st.bad[0]+1)+': '+st.bad[1];
 }else{
  w.className='state ok';
  w.textContent='План проходит правила аддона, кликов '+STEPS.length+'.'
   +(at===null?'':' Новые ранги встают после шага '+(at+1)+'.');
 }
 document.getElementById('cnt').textContent=
  'всего '+STEPS.length+' · сила '+all.cnt[1]+' · ловкость '+all.cnt[2]+
  ' · интеллект '+all.cnt[3];
 var p=document.getElementById('plan'); p.innerHTML='';
 for(var i=0;i<STEPS.length;i++){
  var s=STEPS[i], v=CELLS[key(s)];
  var d=document.createElement('span');
  d.className='chip'+(st.bad&&st.bad[0]==i?' bad':'')+(at===i?' at':'');
  d.innerHTML='<b>'+(i+1)+'</b> '+PLAN.trees[s[0]].slice(0,3).toLowerCase()+
   ' '+s[1]+'\u00b7'+s[2]+' <i>'+(v?short(v.icon):'?')+'</i><u>\u00d7</u>';
  d.querySelector('u').onclick=(function(n){return function(e){
    e.stopPropagation(); del(n);};})(i);
  d.onclick=(function(n){return function(){pick(n);};})(i);
  p.appendChild(d);
 }
 var box=document.getElementById('trees'); box.innerHTML='';
 ['1','2','3'].forEach(function(t){
  var h=document.createElement('h2');
  h.textContent=PLAN.trees[t]+' — рангов '+all.cnt[t];
  box.appendChild(h);
  var g=document.createElement('div'); g.className='grid';
  for(var r=1;r<=7;r++){
   var open=all.cnt[t]>=NEED[r-1];
   for(var c=1;c<=5;c++){
    var k=t+','+r+','+c, v=CELLS[k];
    var b=document.createElement('button');
    if(!v){ b.className='cell none'; b.disabled=true; b.textContent=''; }
    else{
     var n=all.lvl[k]||0;
     b.className='cell'+(n?' took':'')+(open?'':' shut')+
                 (n>=v.max?' full':'');
     b.innerHTML='<b>'+short(v.icon)+'</b><i>'+n+'/'+v.max+'</i>';
     b.title=v.mod+(v.req?(' · нужен '+v.req[0]+' ранга '+v.req[1]):'');
     b.onclick=(function(t,r,c){return function(){add(+t,r,c);};})(t,r,c);
    }
    g.appendChild(b);
   }
  }
  box.appendChild(g);
  var note=document.createElement('div'); note.className='dim';
  note.textContent='ряды открываются с '+NEED.join(', ')+' рангов';
  box.appendChild(note);
 });
 document.getElementById('planjson').value=JSON.stringify(STEPS);
}
draw();
"""




def gsi_title(gsi):
    s = (gsi or "").replace("npc_dota_hero_", "").replace("_", " ").strip()
    return " ".join(w.capitalize() for w in s.split())


def known_heroes():
    d = core.read_json(core.file("talents_grid.json")) or {}
    return sorted((d.get("heroes") or {}).keys())


def have_gsi():
    out = {}
    for n in core.hero_files():
        h = core.read_json(core.hero_path(n)) or {}
        out[h.get("gsi_name") or ""] = n
    return out


def newhero_html():
    busy = have_gsi()
    all_h = known_heroes()
    opts = "".join('<option value="%s">%s%s</option>'
                   % (html.escape(g, quote=True), html.escape(gsi_title(g)),
                      " · билд уже есть" if g in busy else "")
                   for g in all_h)
    src = "".join('<option value="%s"%s>%s</option>'
                  % (html.escape(n, quote=True),
                     " selected" if n == cur_hero() else "",
                     html.escape(n))
                  for n in core.hero_files())
    free = [g for g in all_h if g not in busy]
    return ("".join([
        "<div class='state'>Новый билд делается копией готового: разделы "
        "те же, меняются герой и название. Дальше правится здесь же — "
        "план талантов по сетке, закуп списком, точка пика по снимку."
        "<br><br>Можно сделать и второй билд на того же героя, если дать "
        "ему своё имя файла: обход берёт все билды, у которых снята "
        "точка пика.<br><br>Героев без билда: <b>", str(len(free)),
        "</b> из ", str(len(all_h)), ".</div>",
        "<div class='fld'><label>Герой</label>"
        "<select id='gsi' onchange='suggest()'>", opts, "</select></div>",
        "<div class='fld'><label>Имя файла</label>"
        "<input id='name' placeholder='например drow_tank'>"
        "<div class='dim'>Латиницей, цифры и подчёркивание. Пусто — "
        "возьмётся имя героя.</div></div>",
        "<div class='fld'><label>Взять за основу</label>"
        "<select id='src'>", src, "</select>"
        "<div class='dim'>Скопируются способности, реакции и выключатели. "
        "Точка пика не копируется — её надо снять.</div></div>",
        "<div class='fld'><label>Закуп</label>"
        "<select id='wipe'>"
        "<option value='0'>скопировать из основы</option>"
        "<option value='1'>очистить — соберу сам</option>"
        "</select>"
        "<div class='dim'>Таланты в любом случае раскладываются "
        "под выбранного героя: тридцать рангов в силу, остальное в "
        "ловкость. Поправить можно по сетке.</div></div>",
        "<div class='row'><button class='go' onclick='makeHero()'>"
        "Создать билд</button></div><div id='made' class='dim'></div>"]))


def fresh_plan(gsi):
    try:
        import restyle_builds
    except Exception as e:
        log("план талантов не собран: %s: %s" % (type(e).__name__, e))
        return []
    grid = core.read_json(core.file("talents_grid.json")) or {}
    cells = (grid.get("heroes") or {}).get(gsi or "")
    need = grid.get("row_need") or []
    if not cells or not need:
        return []
    try:
        return restyle_builds.make_plan(cells, need)
    except Exception as e:
        log("план талантов не собран: %s: %s" % (type(e).__name__, e))
        return []


def drop_hero(name):
    if name not in core.hero_files():
        return "нет такого билда"
    if name == "standard":
        return "это образец, с которого делаются новые — его не удаляю"
    try:
        folder = os.path.join(core.LOGS_DIR, "removed")
        os.makedirs(folder, exist_ok=True)
        dst = os.path.join(folder, "%s.json" % name)
        if os.path.exists(dst):
            dst = os.path.join(folder, "%s-%s.json"
                               % (name, time.strftime("%Y%m%d-%H%M%S")))
        shutil.move(core.hero_path(name), dst)
    except OSError as e:
        return "файл не убрался: %s" % e
    fav = [n for n in favorites() if n != name]
    data = core.read_json(LOCAL) or {}
    data["favorites"] = fav
    write_json(LOCAL, data)
    left = core.hero_files()
    if cur_hero() == name and left:
        put_local("hero", left[0])
        log("билд был выбран — переключился на %s" % left[0])
    log("билд %s убран в logs/removed" % name)
    return ""


def make_hero(gsi, src, wipe, name=""):
    if gsi not in known_heroes():
        return "", "нет такого героя в правилах талантов"
    if src not in core.hero_files():
        return "", "нет билда-основы"
    name = (name or gsi.replace("npc_dota_hero_", "")).strip().lower()
    if not re.match(r"^[a-z0-9_]{2,40}$", name):
        return "", "имя файла — латиница, цифры и подчёркивание"
    if name in core.hero_files():
        return "", "билд %s уже есть" % name
    data = core.read_json(core.hero_path(src))
    if not data:
        return "", "основа не читается"
    data = json.loads(json.dumps(data))
    data["title"] = gsi_title(gsi)
    data["gsi_name"] = gsi
    data["pick_at"] = {}
    data.pop("pick_derived", None)
    data["_"] = ("Сделан на пульте копией билда %s. Точка пика не снята: "
                 "сними её на странице билда." % src)
    if wipe:
        data["items"] = []
    plan = fresh_plan(gsi)
    if plan or wipe:
        data.setdefault("talents", {})["plan"] = plan
    write_json(core.hero_path(name), data)
    log("новый билд %s (%s), основа %s, таланты %d"
        % (name, gsi, src, len(((data.get("talents") or {})
                                .get("plan") or []))))
    return name, ""


NEWHERO_JS = """
function suggest(){
 var g=document.getElementById('gsi').value;
 var n=document.getElementById('name');
 if(!n.value) n.placeholder=g.replace('npc_dota_hero_','');
}
function makeHero(){
 var g=document.getElementById('gsi').value;
 var s=document.getElementById('src').value;
 var w=document.getElementById('wipe').value;
 var nm=document.getElementById('name').value;
 var note=document.getElementById('made');
 if(!g){ note.textContent='выбери героя'; return; }
 note.textContent='создаю…';
 fetch('/makehero?k=%(k)s&gsi='+encodeURIComponent(g)+
       '&src='+encodeURIComponent(s)+'&wipe='+w+
       '&name='+encodeURIComponent(nm),{cache:'no-store'})
  .then(function(r){return r.json();})
  .then(function(d){
    if(d.ok){ location.href='/hero?k=%(k)s&name='+encodeURIComponent(d.name); }
    else{ note.textContent='не вышло — '+d.err; note.style.color='#ff8b8b'; }
  })
  .catch(function(){ note.textContent='нет связи'; });
}
"""


def favorites():
    return [n for n in (conf_view().get("favorites") or [])
            if isinstance(n, str)]


def set_fav(name, on):
    if name not in core.hero_files():
        return False
    fav = favorites()
    if on and name not in fav:
        fav.append(name)
    elif not on and name in fav:
        fav.remove(name)
    else:
        return True
    data = core.read_json(LOCAL) or {}
    data["favorites"] = sorted(fav)
    write_json(LOCAL, data)
    log("избранное: %s %s" % (name, "добавлен" if on else "убран"))
    return True


def set_only_fav(on):
    put_local("only_favorites", bool(on))
    return True


def settings_html():
    conf = conf_view()
    diffs = difficulties()
    now_d = conf.get("difficulty")
    dl = []
    for i, d in enumerate(diffs, 1):
        dl.append('<button class="wide%s" onclick="setDiff(%d)">'
                  '<span class="dot%s"></span>%d · %s</button>'
                  % (" go" if i == now_d else "", i,
                     " on" if i == now_d else "", i,
                     html.escape(d.get("name", "?"))))
    now_h = cur_hero()
    fav = set(favorites())
    only = bool(conf_view().get("only_favorites"))
    names = core.hero_files()
    shown = [n for n in names if n in fav] if only else names
    head = ('<button class="wide" onclick="onlyFav(%d)">'
            '<span class="box%s">\u2605</span>Только избранные'
            '<span class="dim"> \u00b7 %d из %d</span></button>'
            % (0 if only else 1, " on" if only else "", len(fav), len(names)))
    hl = [head]
    for n in shown:
        h = core.read_json(core.hero_path(n))
        off = [k for k, v in (h.get("functions") or {}).items() if not v]
        hl.append('<div class="heroRow">'
                  '<button class="star%s" onclick="setFav(this,\'%s\')">'
                  '\u2605</button>'
                  '<button class="grow%s" onclick="setHero(\'%s\')">'
                  '<span class="dot%s"></span>%s<span class="dim"> \u00b7 %s</span>'
                  '</button>'
                  '<a class="edit" href="/hero?k=%s&name=%s">настроить</a></div>'
                  % (" on" if n in fav else "",
                     html.escape(n, quote=True),
                     " go" if n == now_h else "",
                     html.escape(n, quote=True),
                     " on" if n == now_h else "",
                     html.escape(h.get("title") or n),
                     ("выключено: " + ", ".join(off)) if off else "всё включено",
                     TOKEN, urllib.parse.quote(n)))
    if only and not fav:
        hl.append("<p class='dim'>В избранном пусто. Нажми звезду у билда "
                  "— он появится здесь.</p>")
    mode = conf.get("mode") or "solo"
    pconf = conf.get("party") or {}
    pnote = (("пати, роль %s" % ROLE_NAMES.get(pconf.get("role"), "?"))
             if pconf.get("on") else "соло, один бот")
    ml = []
    for key, title, note in MODES:
        ml.append('<button class="wide%s" onclick="setMode(\'%s\')">'
                  '<span class="dot%s"></span>%s<span class="dim"> · %s</span>'
                  '</button>'
                  % (" go" if key == mode else "", key,
                     " on" if key == mode else "",
                     html.escape(title), html.escape(note)))
    return (
        "<div class='state'>Смена режима, билда и сложности применяется "
        "<b>со следующего матча</b>: начатый доигрывается как был.</div>"
        "<h2>Режим</h2>" + "".join(ml)
        + (sweep_html(conf) if mode == "sweep" else "")
        + "<h2>Вдвоём</h2>"
          "<button class='wide' onclick='go(&quot;party&quot;)'>Соло или пати"
          "<span class='dim'> · сейчас %s</span></button>" % pnote +
        "<h2>Игра и компьютер</h2>%s"
        "<h2>Функции закупа</h2>"
        "<button class='wide' onclick=\"go('roles')\">Настройки функций"
        "<span class='dim'> \u00b7 что делают и на какой клавише</span>"
        "</button>"
        "<h2>Механика</h2>"
        "<button class='wide' onclick='go(&quot;common&quot;)'>Общие "
        "настройки<span class='dim'> · тайминги, магазин, волны, "
        "наблюдатель, пульт</span></button>"
        "<button class='wide' onclick='go(&quot;coords&quot;)'>Координаты "
        "экрана<span class='dim'> · точки клика профиля %s, снимаются "
        "по снимку</span></button>"
        "<h2>Сложность</h2>%s<h2>Герои</h2>"
        "<button class='wide' onclick='go(&quot;newhero&quot;)'>"
        "Новый билд<span class='dim'> · копией готового, под "
        "героя без билда</span></button>%s"
        % (game_html(), html.escape(screen_profile() or "?"),
           "".join(dl) or "<p>нет списка сложностей</p>",
           "".join(hl) or "<p>В папке heroes/ нет ни одного билда.</p>"))


def hero_html(name):
    if name not in core.hero_files():
        return "<p>Нет такого билда.</p>"
    h = core.read_json(core.hero_path(name))
    funcs = h.get("functions", {})
    sw = []
    for key, label in FUNC_LABEL:
        val = funcs.get(key, True)
        sw.append('<button class="wide%s" onclick="setFn(this,\'%s\')">'
                  '%s<span class="dim"> · %s</span></button>'
                  % (" go" if val else "", key, html.escape(label),
                     "вкл" if val else "выкл"))
    flds, pick = [], []
    for path, val, note in walk_fields(h):
        if path == "pick_at":
            continue
        if val is None:
            head3, hint = field_text(path, note)
            flds.append("<h3>%s</h3>" % html.escape(head3))
            if hint:
                flds.append("<div class='dim' style='margin:-4px 0 10px'>%s</div>" % html.escape(hint))
            continue
        if path.startswith("pick_at.") and is_point(val):
            pick.append(field_html(path, val, note))
            continue
        flds.append(field_html(path, val, note))
    if not pick:
        pick.append("<p class='dim'>В билде нет поля pick_at — "
                    "снимется в оба профиля сразу.</p>")
    pick.insert(0, grab_button("pick_at", name)
                + "<div id='grab' class='dim'></div>")
    head = ("<div class='state'>билд <b>%s</b><br>heroes/%s.json%s</div>"
            % (html.escape(h.get("title") or name), html.escape(name),
               "<br>это текущий билд" if name == cur_hero() else ""))
    return (head
            + "<h2>Что делать за этого героя</h2>" + "".join(sw)
            + "<h2>Закуп по порядку</h2>" + items_editor(h)
            + "<p class='dim'>Цена подставляется сама: что бот замерил при "
              "покупке, иначе расчёт по файлам игры. Клавиши функций — "
              "в «Настройках функций», там же слот по умолчанию для "
              "каждой роли.</p>"
            + "<h2>Точка пика</h2>"
              "<p class='dim'>Куда кликать на экране выбора героя. "
              "Снимай с игрой на экране: бот берёт точку из-под "
              "курсора и сам раскладывает её по профилям."
              "</p>" + "".join(pick)
            + "<h2>План талантов</h2>"
              "<button class='wide' onclick='go(&quot;talents?name="
            + urllib.parse.quote(name)
            + "&quot;)'>Разложить таланты по сетке"
              "<span class='dim'> · как в игре, с проверкой правил"
              "</span></button>"
            + "<h2>Способности и реакции</h2>" + "".join(flds)
            + "<div class='row'><button class='go' onclick='saveHero()'>"
              "Сохранить</button></div><div id='saved' class='dim'></div>"
            + "<h2>Убрать билд</h2>"
              "<p class='dim'>Файл переедет в logs\\removed, из списка "
              "билд исчезнет. Вернуть — положить файл обратно в heroes.</p>"
              "<div class='row'><button class='stop' onclick='dropHero()'>"
              "Удалить билд</button></div>")


def stats_html(with_hidden=False):
    runs = stats.all_runs(with_hidden=with_hidden)
    nhidden = len(stats.hidden())
    if not runs:
        empty = "<p>Матчей в логах пока нет.</p>"
        if nhidden and not with_hidden:
            empty += ('<p><a href="/stats?k=%s&all=1">показать скрытые (%d)</a></p>'
                      % (TOKEN, nhidden))
        return empty
    t = stats.totals([r for r in runs if not r.get("hidden")])
    head = (
        "<div class='state'>"
        "<b>%d матч(ей)</b>, план выполнен полностью в %d<br>"
        "волн <b>%d</b> (%.1f за матч по %d матчам), боссов %d<br>"
        "BKB %d из %d попыток (%d%%), смертей %d, сатаник %d<br>"
        "в игре всего %s</div>"
        % (t["matches"], t["full"], t["waves"], t["per_match"], t["counted"],
           t["bosses"], t["bkb"], t["bkb"] + t["bkb_skipped"], t["bkb_rate"],
           t["deaths"], t["satanic"], stats.hms(t["secs"])))
    rows = []
    for r in runs:
        full = (r["talents_plan"] and r["talents"] >= r["talents_plan"]
                and r["items_plan"] and r["items"] >= r["items_plan"])
        gone = r.get("hidden")
        cls = "gone" if gone else ("ok" if full else "bad")
        btn = ('<button class="del" onclick="hide(this,\'%s\',%d)">%s</button>'
               % (html.escape(r["key"], quote=True), 0 if gone else 1,
                  "вернуть" if gone else "×"))
        rows.append(
            "<tr class='%s'><td>%s</td><td>%s</td><td>%s</td><td>%d</td>"
            "<td>%d</td><td>%d</td><td>%d</td><td>%d/%d</td><td>%d/%d</td>"
            "<td>%d</td><td>%s</td></tr>"
            % (cls, time.strftime("%d.%m %H:%M", time.localtime(r["end"])),
               html.escape(r.get("hero") or "?"),
               stats.hms(r["secs"]), r["waves"], r["bosses"], r["bkb"],
               r["deaths"], r["talents"], r["talents_plan"],
               r["items"], r["items_plan"], r["level"], btn))
    if with_hidden:
        tail = '<p><a href="/stats?k=%s">только видимые</a></p>' % TOKEN
    elif nhidden:
        tail = ('<p><a href="/stats?k=%s&all=1">показать скрытые (%d)</a></p>'
                % (TOKEN, nhidden))
    else:
        tail = ""
    return head + (
        "<table><tr><th>конец</th><th>герой</th><th>длит</th>"
        "<th>волн</th><th>босс</th>"
        "<th>BKB</th><th>смр</th><th>талант</th><th>предм</th><th>ур</th>"
        "<th></th></tr>%s</table>" % "".join(rows)) + tail


PAGE = """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>WoDOTA</title>
<style>
 *{box-sizing:border-box}
 :root{color-scheme:dark;
  --bg:#0e1013;--card:#171a21;--card2:#1e222b;--line:#272c38;
  --tx:#e8eaed;--dim:#98a1b0;--accent:#34d399;--blue:#7aa2ff;
  --warn:#fbbf24;--bad:#f87171;--pad:14px}
 body{margin:0;background:var(--bg);color:var(--tx);
  font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  padding:0 0 78px;-webkit-text-size-adjust:100%%}
 .bar{position:sticky;top:0;z-index:5;display:flex;align-items:center;
  gap:10px;padding:10px var(--pad);background:rgba(14,16,19,.94);
  border-bottom:1px solid var(--line)}
 .brand{flex:1;min-width:0;font-weight:600}
 .brand i{display:block;font:400 12px/1.3 inherit;color:var(--dim);
  font-style:normal;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .pw{flex:none;min-height:38px;padding:0 16px;border:0;border-radius:999px;
  font:600 13px/1 inherit;background:#1d6b4e;color:#e9fff6}
 .pw.danger{background:#532a2e;color:#ffc2c2}
 main{padding:var(--pad)}
 .live{display:flex;align-items:center;gap:8px;flex-wrap:wrap;
  padding:10px 12px;margin:0 var(--pad) 14px;border-radius:12px;
  background:var(--card);border:1px solid var(--line);font-size:13px}
 .live b{font-weight:600}
 .dot2{width:9px;height:9px;border-radius:50%%;background:var(--bad);
  box-shadow:0 0 0 3px rgba(248,113,113,.15);flex:none}
 .dot2.on{background:var(--accent);box-shadow:0 0 0 3px rgba(52,211,153,.15)}
 nav{position:fixed;left:0;right:0;bottom:0;z-index:6;display:flex;
  gap:2px;padding:6px 6px calc(6px + env(safe-area-inset-bottom));
  background:rgba(14,16,19,.96);border-top:1px solid var(--line)}
 nav a{flex:1;text-align:center;text-decoration:none;color:var(--dim);
  font-size:11px;padding:7px 2px;border-radius:12px}
 nav a u{display:block;font-size:17px;line-height:1.2;text-decoration:none}
 nav a.cur{color:var(--accent);background:rgba(52,211,153,.10)}
 h1{display:none}
 h2{font:600 12px/1.3 inherit;letter-spacing:.08em;text-transform:uppercase;
  color:var(--dim);margin:22px 0 10px}
 h2:first-child{margin-top:4px}
 h3{font:600 13px/1.3 inherit;color:var(--blue);margin:18px 0 8px}
 a{color:var(--blue)}
 .state{padding:12px 14px;border-radius:14px;background:var(--card);
  border:1px solid var(--line);margin-bottom:14px;font-size:14px}
 .row{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px}
 .row button{flex:1 1 44%%}
 button{min-height:46px;padding:12px 14px;border:0;border-radius:12px;
  background:var(--card2);color:var(--tx);font:500 14px/1.2 inherit;
  border:1px solid var(--line)}
 button:active{transform:scale(.99)}
 button.go{background:#1d6b4e;border-color:#24855f;color:#eafff6}
 button.stop{background:#532a2e;border-color:#6b373c;color:#ffc2c2}
 button.wide{display:block;width:100%%;text-align:left;margin-bottom:8px}
 button.wide.go{background:rgba(52,211,153,.12);border-color:rgba(52,211,153,.45);
  color:var(--tx)}
 .dot{display:inline-block;width:13px;height:13px;border-radius:50%%;
  border:2px solid #5b6477;margin-right:10px;vertical-align:-2px}
 .dot.on{border-color:var(--accent);background:var(--accent)}
 .dim{color:var(--dim);font-size:13px}
 img{width:100%%;border-radius:14px;display:block;background:#000;
  border:1px solid var(--line)}
 pre{background:#0a0c10;border:1px solid var(--line);padding:12px;
  border-radius:14px;overflow:auto;font:12px/1.5 ui-monospace,Consolas,monospace;
  white-space:pre-wrap;max-height:58vh;margin:14px 0 0}
 table{width:100%%;border-collapse:collapse;font-size:13px;margin-bottom:14px}
 th,td{padding:7px 5px;text-align:right;border-bottom:1px solid var(--line);
  white-space:nowrap}
 th{color:var(--dim);font-weight:500;font-size:12px}
 td:first-child,th:first-child{text-align:left}
 tr.bad td{color:var(--warn)}
 tr.ok td{color:var(--accent)}
 tr.gone td{color:#5f6878;text-decoration:line-through}
 tr.gone td:last-child{text-decoration:none}
 td:last-child,th:last-child{width:1%%;padding-left:10px}
 button.del{min-height:0;flex:none;padding:5px 10px;font-size:12px;
  background:#3a2b2f;color:#e6a0a0;border-color:#4d383c;border-radius:9px}
 .files{margin-bottom:12px;font-size:13px}
 .files a{display:inline-block;margin:0 10px 8px 0}
 .files a.cur{color:var(--accent);text-decoration:none}
 .heroRow{display:flex;gap:8px;margin-bottom:8px}
 .heroRow .grow{flex:1;text-align:left}
 .heroRow .edit{flex:none;display:flex;align-items:center;padding:0 16px;
  background:var(--card2);border:1px solid var(--line);border-radius:12px;
  text-decoration:none;font-size:13px;color:var(--blue)}
 .fld{margin-bottom:14px}
 .fld label{display:block;font-size:13px;color:#c7cedb;margin-bottom:5px}
 .fld input,.fld select,.fld textarea{width:100%%;padding:11px 12px;
  font:13px/1.45 ui-monospace,Consolas,monospace;background:#0a0c10;
  color:var(--tx);border:1px solid var(--line);border-radius:10px}
 .fld textarea{min-height:46px}
 .fld .dim{margin-top:5px;font-size:12px}
 button.pick{display:block;width:100%%;min-height:0;margin:-8px 0 14px;
  padding:9px 12px;font-size:13px;background:rgba(122,162,255,.10);
  border-color:rgba(122,162,255,.35);color:#b9ccff}
 .chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
 .chip{display:inline-flex;align-items:center;gap:5px;padding:5px 8px;
  border-radius:10px;background:var(--card2);border:1px solid var(--line);
  font-size:12px}
 .chip b{color:var(--dim);font-weight:500}
 .chip i{font-style:normal;color:var(--blue)}
 .chip u{text-decoration:none;color:#e6a0a0;padding:0 2px;cursor:pointer}
 .chip.bad{border-color:var(--bad);background:rgba(248,113,113,.12)}
 .chip.at{border-color:var(--blue);background:rgba(122,162,255,.16)}
 .state.bad{border-color:var(--bad);background:rgba(248,113,113,.12);
  color:#ffd4d4}
 .state.ok{border-color:rgba(52,211,153,.4);background:rgba(52,211,153,.10)}
 .grid{display:grid;grid-template-columns:repeat(5,1fr);gap:5px;
  margin-bottom:8px}
 button.cell{min-height:52px;padding:5px 3px;border-radius:10px;
  background:var(--card);border:1px solid var(--line);
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:2px;overflow:hidden}
 button.cell b{font:600 10px/1.15 inherit;color:var(--tx);word-break:break-all;
  max-width:100%%;text-align:center}
 button.cell i{font:400 10px/1 inherit;font-style:normal;color:var(--dim)}
 button.cell.none{background:transparent;border-color:transparent}
 button.cell.took{border-color:rgba(52,211,153,.5);
  background:rgba(52,211,153,.10)}
 button.cell.took i{color:var(--accent)}
 button.cell.full{border-color:rgba(52,211,153,.9)}
 button.cell.shut{opacity:.45}
 .heroRow .star{flex:none;width:48px;padding:0;font-size:17px;
  color:#4a5365;background:var(--card);border:1px solid var(--line)}
 .heroRow .star.on{color:var(--warn);border-color:rgba(251,191,36,.45);
  background:rgba(251,191,36,.10)}
 .box{display:inline-block;width:22px;height:22px;line-height:20px;
  text-align:center;border:1px solid #4a5365;border-radius:6px;
  color:#4a5365;font-size:13px;margin-right:10px;vertical-align:-4px}
 .box.on{color:var(--warn);border-color:rgba(251,191,36,.6);
  background:rgba(251,191,36,.12)}
 .hid{display:none}
 .irow{display:flex;gap:8px;align-items:flex-start;margin-bottom:10px}
 .irow .num{flex:none;width:22px;padding-top:12px;color:var(--dim);
  font-size:13px;text-align:right}
 .irow .igrow{flex:1;min-width:0}
 .irow select{width:100%%;padding:10px 11px;margin-bottom:5px;
  background:#0a0c10;color:var(--tx);border:1px solid var(--line);
  border-radius:10px;font-size:14px}
 .irow .ibtn{flex:none;display:flex;flex-direction:column;gap:5px}
 .irow .ibtn button{min-height:0;padding:6px 10px;font-size:13px}
 @media (max-width:520px){
  table{font-size:12px}
  th,td{padding:6px 2px}
 }
</style>
<header class="bar">
 <div class="brand">WoDOTA<i>%(title)s</i></div>
 <button id="pw" class="pw" onclick="power()">…</button>
</header>
<div class="live"><span id="dot" class="dot2"></span><b id="st">…</b>
 <span id="up" class="dim"></span><span id="note" class="dim"></span></div>
<main>%(body)s</main>
<nav>%(nav)s</nav>
<script>
var K='%(k)s';
function go(a){location.href='/'+a+(a.indexOf('?')>=0?'&':'?')+'k='+K;}
function paint(d){
 var on=!!d.alive;
 document.getElementById('dot').className='dot2'+(on?' on':'');
 document.getElementById('st').textContent=on?'бот работает':'бот выключен';
 document.getElementById('up').textContent=d.up?'· '+d.up:'';
 document.getElementById('note').textContent=d.note||'';
 var b=document.getElementById('pw');
 b.textContent=on?'Выключить':'Запустить';
 b.className='pw'+(on?' danger':'');
}
function act(a,then){
 return fetch('/'+a+'?k='+K+'&x=1',{cache:'no-store'})
  .then(function(r){return r.json();})
  .then(function(d){paint(d);if(then)then(d);return d;})
  .catch(function(){});
}
function power(){
 var b=document.getElementById('pw');
 var on=b.className.indexOf('danger')>=0;
 b.textContent='…';
 act(on?'stop':'start');
}
function beat(){
 fetch('/state?k='+K,{cache:'no-store'})
  .then(function(r){return r.json();}).then(paint)
  .catch(function(){})
  .then(function(){setTimeout(beat,5000);});
}
paint(%(st)s);
setTimeout(beat,5000);
%(js)s
</script>
</html>"""

TABS = [("", "\u25c9", "Автоигра"), ("settings", "\u2699", "Настройки"),
        ("hero", "\u2b22", "Билд"), ("stats", "\u25a6", "Статистика"),
        ("log", "\u2261", "Логи")]


TAB_OF = {"common": "settings", "coords": "settings",
          "roles": "settings", "party": "settings",
          "talents": "hero", "newhero": "hero", "hero": "hero"}


def nav_html(cur):
    out = []
    for path, icon, label in TABS:
        out.append('<a class="%s" href="/%s?k=%s"><u>%s</u>%s</a>'
                   % ("cur" if path == cur else "", path, TOKEN, icon,
                      html.escape(label)))
    return "".join(out)


HOME_BODY = """<div class="row">
  <button onclick="act('shot')">Снимок экрана</button>
  <button onclick="act('restart')">Перезапустить бота</button>
</div>
<img id="pic"%(shot)s>
<pre id="log">%(log)s</pre>"""

SUB_BODY = """%(inner)s"""

HERO_JS = """
function setFav(b, name){
 var on = b.className.indexOf('on') < 0;
 fetch('/setfav?k='+K+'&name='+encodeURIComponent(name)+'&on='+(on?1:0),
       {cache:'no-store'})
  .then(function(){ b.className = 'star' + (on ? ' on' : ''); })
  .catch(function(){});
}
function onlyFav(v){
 fetch('/onlyfav?k='+K+'&on='+v,{cache:'no-store'})
  .then(function(){ location.reload(); })
  .catch(function(){});
}
function setHero(name){
 fetch('/sethero?k=%(k)s&name='+encodeURIComponent(name),{cache:'no-store'})
  .then(function(r){return r.json();})
  .then(function(d){ if(d.ok) location.reload(); });
}
function retry(b,hero){
 b.disabled=true;
 fetch('/retry?k=%(k)s&hero='+encodeURIComponent(hero),{cache:'no-store'})
  .then(function(r){return r.json();})
  .then(function(d){ if(d.ok) location.reload(); else b.disabled=false; })
  .catch(function(){ b.disabled=false; });
}
function setMode(name){
 fetch('/setmode?k=%(k)s&name='+encodeURIComponent(name),{cache:'no-store'})
  .then(function(r){return r.json();})
  .then(function(d){ if(d.ok) location.reload(); });
}
function setDiff(n){
 fetch('/setdiff?k=%(k)s&n='+n,{cache:'no-store'})
  .then(function(r){return r.json();})
  .then(function(d){ if(d.ok) location.reload(); });
}
function setFn(b,key){
 var on = b.className.indexOf('go')<0;
 var who = new URLSearchParams(location.search).get('name') || '';
 fetch('/setfn?k=%(k)s&hero='+encodeURIComponent(who)
       +'&name='+encodeURIComponent(key)+'&on='+(on?1:0),{cache:'no-store'})
  .then(function(r){return r.json();})
  .then(function(d){
    if(!d.ok) return;
    b.className = 'wide' + (d.on ? ' go' : '');
    b.querySelector('.dim').textContent = ' · ' + (d.on ? 'вкл' : 'выкл');
  });
}
"""

MACHINE_JS = """
function taskNote(t){ document.getElementById('task').textContent = t; }
function poll(){
 fetch('/taskstate?k=%(k)s',{cache:'no-store'})
  .then(function(r){return r.json();})
  .then(function(d){ taskNote(d.text); if(d.busy) setTimeout(poll,2000); });
}
function act2(path,label){
 taskNote(label+'…');
 fetch(path+'?k=%(k)s',{cache:'no-store'})
  .then(function(r){return r.json();})
  .then(function(d){ taskNote(d.text); setTimeout(poll,2000); })
  .catch(function(){ taskNote('нет связи'); });
}
var offCode='';
function poweroff(){
 var b=document.getElementById('off'), h=document.getElementById('offhint');
 if(offCode){ var c=offCode; offCode='';
   b.textContent='Выключаю…';
   fetch('/poweroff?k=%(k)s&code='+encodeURIComponent(c),{cache:'no-store'})
    .then(function(r){return r.json();}).then(function(d){ taskNote(d.text); });
   return; }
 fetch('/armoff?k=%(k)s',{cache:'no-store'})
  .then(function(r){return r.json();})
  .then(function(d){
    offCode=d.code;
    b.firstChild.textContent='Точно выключить?';
    h.textContent=' \u00b7 нажми ещё раз, осталось '+d.sec+' с';
    setTimeout(function(){ if(offCode){ offCode='';
      b.firstChild.textContent='Выключить компьютер';
      h.textContent=' \u00b7 нажми, потом подтверди'; } }, d.sec*1000);
  });
}
"""

ITEMS_JS = """
// Редактор закупа. Строки рисуются здесь, а не на сервере: триста с
// лишним предметов в каждом выпадающем списке раздули бы страницу до
// сотен килобайт, а так список приходит один раз.
function itemCost(name, fixed){
 if(fixed) return fixed;
 if(ITEMDATA.learned[name]) return ITEMDATA.learned[name];
 for(var i=0;i<ITEMDATA.all.length;i++) if(ITEMDATA.all[i].n==name) return ITEMDATA.all[i].c;
 return 0;
}
function costNote(name, fixed){
 if(fixed) return fixed + ' вписано';
 if(ITEMDATA.learned[name]) return ITEMDATA.learned[name] + ' замерено';
 var c = itemCost(name, 0);
 return c ? (c + ' по файлам') : 'цена неизвестна';
}
function drawItems(){
 var box=document.getElementById('itemed'); box.innerHTML='';
 ITEMDATA.rows.forEach(function(r,i){
  var d=document.createElement('div'); d.className='irow';
  var opts='';
  for(var j=0;j<ITEMDATA.all.length;j++){
   var a=ITEMDATA.all[j];
   opts+='<option value="'+a.n+'"'+(a.n==r.name?' selected':'')+'>'+a.t+'</option>';
  }
  if(r.name && !ITEMDATA.all.some(function(a){return a.n==r.name;}))
   opts='<option value="'+r.name+'" selected>'+r.name+'</option>'+opts;
  var rop='<option value="">носить</option>', role=null;
  ITEMDATA.roles.forEach(function(x){
   if(x.k==r.role) role=x;
   rop+='<option value="'+x.k+'"'+(x.k==r.role?' selected':'')+'>'+x.t+'</option>';
  });
  // Слот показываем только там, где он что-то значит: «носить» и
  // докуп книг никуда не перекладывают.
  var sop='', hint='';
  if(role && role.s){
   sop='<select class="islot"><option value="">'+
       'слот по умолчанию \u00b7 '+role.s+'</option>';
   for(var sl=1;sl<=6;sl++)
    sop+='<option value="'+sl+'"'+(r.slot==sl?' selected':'')+
         '>слот '+sl+'</option>';
   sop+='</select>';
   hint=(r.role=='boss_swap')
    ? 'на боссе предмет переедет в этот слот, потом вернётся'
    : 'из этого слота бот жмёт клавишу роли';
   hint='<div class="dim">'+hint+'</div>';
  }
  d.innerHTML =
   '<span class="num">'+(i+1)+'</span>'+
   '<div class="igrow">'+
     '<select class="iname">'+opts+'</select>'+
     '<select class="irole">'+rop+'</select>'+ sop + hint +
     '<div class="dim icost">'+costNote(r.name,r.cost)+'</div>'+
   '</div>'+
   '<div class="ibtn">'+
     '<button onclick="moveRow('+i+',-1)">\\u2191</button>'+
     '<button onclick="moveRow('+i+',1)">\\u2193</button>'+
     '<button class="del" onclick="dropRow('+i+')">\\u00d7</button>'+
   '</div>';
  d.querySelector('.iname').onchange=function(){
   ITEMDATA.rows[i].name=this.value; ITEMDATA.rows[i].cost=0; drawItems(); };
  d.querySelector('.irole').onchange=function(){
   ITEMDATA.rows[i].role=this.value;
   ITEMDATA.rows[i].slot=0;   // у новой роли свой слот по умолчанию
   drawItems(); };
  var sel=d.querySelector('.islot');
  if(sel) sel.onchange=function(){
   ITEMDATA.rows[i].slot=this.value?parseInt(this.value,10):0;
   sync(); };
  box.appendChild(d);
 });
 sync();
}
function sync(){
 var out=ITEMDATA.rows.filter(function(r){return r.name;}).map(function(r){
  var o={name:r.name};
  if(r.role) o.role=r.role;
  if(r.slot) o.slot=r.slot;
  if(r.cost) o.cost=r.cost;
  // Всё, чего нет в форме, тащим как есть: у предмета бывает stash,
  // своё пояснение и прочее. Без этого правка списка с телефона
  // молча стирала бы их — так мидас терял «в тайник», а бабочка
  // фуриона слот на боссе.
  for(var k in r)
   if(k!='name'&&k!='role'&&k!='slot'&&k!='cost') o[k]=r[k];
  return o;
 });
 document.getElementById('itemsjson').value=JSON.stringify(out);
}
function moveRow(i,d){
 var j=i+d; if(j<0||j>=ITEMDATA.rows.length) return;
 var t=ITEMDATA.rows[i]; ITEMDATA.rows[i]=ITEMDATA.rows[j]; ITEMDATA.rows[j]=t;
 drawItems();
}
function dropRow(i){ ITEMDATA.rows.splice(i,1); drawItems(); }
function addRow(){
 ITEMDATA.rows.push({name:(ITEMDATA.all[0]||{}).n||'',role:'',
                     slot:0,cost:0});
 drawItems();
}
if(typeof ITEMDATA!=='undefined') drawItems();
"""

ROLES_JS = """
function saveRoles(){
 var out={}, el=document.querySelectorAll('[data-p]');
 for(var i=0;i<el.length;i++){ out[el[i].getAttribute('data-p')]=el[i].value; }
 var note=document.getElementById('saved');
 note.textContent='сохраняю…';
 fetch('/saveroles?k=%(k)s',{method:'POST',cache:'no-store',
                             body:JSON.stringify(out)})
  .then(function(r){return r.json();})
  .then(function(d){
    note.textContent = d.ok ? 'сохранено' : ('не сохранено — '+d.err);
    note.style.color = d.ok ? '#6ee7a0' : '#ff8b8b';
  })
  .catch(function(){ note.textContent='не сохранено — нет связи'; });
}
"""

COMMON_JS = """
function saveCommon(){ postFields('/savecommon?k=%(k)s'); }
"""


POST_JS = """
function postFields(url){
 var out={}, el=document.querySelectorAll('[data-p]');
 for(var i=0;i<el.length;i++){ out[el[i].getAttribute('data-p')]=el[i].value; }
 var note=document.getElementById('saved');
 note.textContent='сохраняю…';
 fetch(url,{method:'POST',cache:'no-store',body:JSON.stringify(out)})
  .then(function(r){return r.json();})
  .then(function(d){
    note.textContent = d.ok ? 'сохранено' : ('не сохранено — '+d.err);
    note.style.color = d.ok ? '#6ee7a0' : '#ff8b8b';
  })
  .catch(function(){ note.textContent='не сохранено — нет связи'; });
}
"""

SAVE_JS = """
function dropHero(){
 var name='%(n)s';
 if(!confirm('Убрать билд? Файл переедет в logs\\removed, из списка он '+
             'исчезнет. Вернуть можно, положив файл обратно в heroes.'))
  return;
 fetch('/delhero?k=%(k)s&name='+name,{cache:'no-store'})
  .then(function(r){return r.json();})
  .then(function(d){
    if(d.ok){ location.href='/settings?k=%(k)s'; }
    else{ var note=document.getElementById('saved');
          note.style.color='#ff8b8b';
          note.textContent='не вышло — '+d.err; }
  })
  .catch(function(){});
}
function saveHero(){
 var out={}, el=document.querySelectorAll('[data-p]');
 for(var i=0;i<el.length;i++){ out[el[i].getAttribute('data-p')]=el[i].value; }
 var note=document.getElementById('saved');
 note.textContent='сохраняю…';
 fetch('/savehero?k=%(k)s&name=%(n)s',
       {method:'POST',cache:'no-store',body:JSON.stringify(out)})
  .then(function(r){return r.json();})
  .then(function(d){
    note.textContent = d.ok ? 'сохранено' : ('не сохранено — '+d.err);
    note.style.color = d.ok ? '#6ee7a0' : '#ff8b8b';
  })
  .catch(function(){ note.textContent='не сохранено — нет связи'; });
}
"""


HIDE_JS = """
function hide(b,id,on){
 b.disabled=true;
 fetch('/hide?k=%(k)s&id='+encodeURIComponent(id)+'&on='+on,{cache:'no-store'})
  .then(function(r){return r.json();})
  .then(function(d){
    if(!d.ok){b.disabled=false;return;}
    var tr=b.closest('tr');
    if(on){tr.remove();}else{tr.className='ok';b.remove();}
  })
  .catch(function(){b.disabled=false;});
}
"""

SCROLL_JS = "var l=document.getElementById('log');if(l)l.scrollTop=l.scrollHeight;"

LIVE_JS = """
var pos=%(pos)d, shot=%(shot)d, L=document.getElementById('log');
if(L)L.scrollTop=L.scrollHeight;
function atEnd(){return L.scrollHeight-L.scrollTop-L.clientHeight<40;}
// Общая отрисовка шапки лежит в самой странице; здесь к ней добавляется
// только то, что есть на главной: снимок экрана и хвост лога.
var barPaint=paint;
paint=function(d){
 barPaint(d);
 if(d.shot&&d.shot!=shot){shot=d.shot;var i=document.getElementById('pic');
  i.src='/img?k='+K+'&t='+shot; i.style.display='block';}
};
function tick(){
 fetch('/tail?k='+K+'&from='+pos,{cache:'no-store'})
  .then(function(r){return r.json();})
  .then(function(d){
    if(d.add){
      // Если читаешь выше — не дёргаем вниз; прилипаем только у конца.
      var end=atEnd(); L.append(d.add); if(end)L.scrollTop=L.scrollHeight;
    }
    pos=d.pos; paint(d);})
  .catch(function(){})
  .then(function(){setTimeout(tick,2000);});
}
setTimeout(tick,2000);
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, body, ctype="text/html; charset=utf-8", code=200):
        if isinstance(body, str):
            body = body.encode("utf-8")
        enc = None
        if (len(body) > 1024
                and "gzip" in self.headers.get("Accept-Encoding", "")
                and not ctype.startswith("image/")):
            body = gzip.compress(body, 6)
            enc = "gzip"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if enc:
            self.send_header("Content-Encoding", enc)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def page(self, title, body, js=""):
        tab = urllib.parse.urlparse(self.path).path.strip("/")
        self.send(PAGE % {
            "title": title, "body": body, "js": js, "k": TOKEN,
            "nav": nav_html(TAB_OF.get(tab, tab)),
            "st": json.dumps(self.state(), ensure_ascii=False)})

    def send_json(self, data):
        self.send(json.dumps(data, ensure_ascii=False),
                  "application/json; charset=utf-8")

    def uptime_text(self):
        up = bot.uptime()
        return (" · %s" % up) if up else ""

    def note_text(self, note=""):
        parts = [note] if note else []
        if bot.restarts:
            parts.append("перезапусков: %d" % bot.restarts)
        if bot.note:
            parts.append(bot.note)
        return " · ".join(parts)

    def state(self, note="", add=None, pos=None):
        d = {"alive": bot.alive, "up": bot.uptime(),
             "note": self.note_text(note), "shot": shot_stamp()}
        if pos is not None:
            d["pos"] = pos
        if add:
            d["add"] = add
        return d

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if q.get("k", [""])[0] != TOKEN:
            self.send("нужен токен", code=403)
            return
        if u.path not in ("/savehero", "/saveroles", "/savecommon",
                          "/savecoords", "/saveparty"):
            self.send("нет такой страницы", code=404)
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
            fields = json.loads(self.rfile.read(size).decode("utf-8"))
        except Exception as e:
            self.send_json({"ok": False, "err": "тело запроса: %s" % e})
            return
        if u.path == "/saveroles":
            err = save_roles(fields)
        elif u.path == "/savecommon":
            err = save_common(fields)
        elif u.path == "/savecoords":
            err = save_coords(fields)
        elif u.path == "/saveparty":
            err = save_party(fields)
        else:
            err = save_hero(q.get("name", [""])[0], fields)
        self.send_json({"ok": not err, "err": err})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if q.get("k", [""])[0] != TOKEN:
            self.send("нужен токен", code=403)
            return

        if u.path == "/img":
            try:
                with open(SHOT, "rb") as f:
                    self.send(f.read(), "image/jpeg")
            except Exception:
                self.send("снимка нет", code=404)
            return

        if u.path == "/settings":
            self.page("настройки", SUB_BODY % {"inner": settings_html()},
                      HERO_JS % {"k": TOKEN} + MACHINE_JS % {"k": TOKEN})
            return

        if u.path == "/hero":
            name = q.get("name", [cur_hero()])[0]
            self.page("билд", SUB_BODY % {"inner": hero_html(name)},
                      HERO_JS % {"k": TOKEN}
                      + SAVE_JS % {"k": TOKEN,
                                   "n": urllib.parse.quote(name)}
                      + ITEMS_JS
                      + POINT_JS % {"k": TOKEN,
                                    "d": int(point_delay())})
            return

        if u.path == "/taskstate":
            self.send_json({"busy": task.busy, "text": task.text})
            return

        if u.path == "/dotarestart":
            ok, text = task.run("Перезапускаю Dota", machine.restart_dota)
            self.send_json({"ok": ok, "text": text})
            return

        if u.path == "/addoncheck":
            wid = workshop_id()

            def check():
                a = machine.addon_state(wid)
                if not a.get("ok"):
                    return a.get("why", "не прочитал")
                if a["fresh"]:
                    return "кастомка актуальна (скачана %s)" % a["updated"]
                return "вышло обновление — " + machine.restart_dota()

            ok, text = task.run("Проверяю кастомку", check)
            self.send_json({"ok": ok, "text": text})
            return

        if u.path == "/armoff":
            self.send_json({"code": arm_poweroff(), "sec": OFF_WINDOW})
            return

        if u.path == "/poweroff":
            self.send_json({"text": do_poweroff(q.get("code", [""])[0])})
            return

        if u.path == "/roles":
            self.page("функции", SUB_BODY % {"inner": roles_html()},
                      ROLES_JS % {"k": TOKEN})
            return

        if u.path == "/common":
            self.page("общие настройки",
                      SUB_BODY % {"inner": common_html()},
                      POST_JS + COMMON_JS % {"k": TOKEN})
            return

        if u.path == "/coords":
            self.page("координаты",
                      SUB_BODY % {"inner": coords_html()},
                      POST_JS + POINT_JS % {"k": TOKEN,
                                            "d": int(point_delay())})
            return

        if u.path == "/party":
            self.page("пати", SUB_BODY % {"inner": party_html()},
                      POST_JS + PARTY_JS % {"k": TOKEN})
            return

        if u.path == "/setparty":
            self.send_json({"ok": set_party(
                q.get("on", ["0"])[0] != "0")})
            return

        if u.path == "/setprole":
            self.send_json({"ok": set_prole(q.get("role", [""])[0])})
            return

        if u.path == "/newhero":
            self.page("новый билд",
                      SUB_BODY % {"inner": newhero_html()},
                      NEWHERO_JS % {"k": TOKEN})
            return

        if u.path == "/delhero":
            err = drop_hero(q.get("name", [""])[0])
            self.send_json({"ok": not err, "err": err})
            return

        if u.path == "/makehero":
            name, err = make_hero(q.get("gsi", [""])[0],
                                  q.get("src", [""])[0],
                                  q.get("wipe", ["1"])[0] != "0",
                                  q.get("name", [""])[0])
            self.send_json({"ok": not err, "err": err, "name": name})
            return

        if u.path == "/talents":
            name = q.get("name", [cur_hero()])[0]
            self.page("таланты", SUB_BODY % {"inner": talents_html(name)},
                      TALENTS_JS
                      + SAVE_JS % {"k": TOKEN,
                                   "n": urllib.parse.quote(name)})
            return

        if u.path == "/takepoint":
            at, err = take_point(q.get("path", [""])[0],
                                 q.get("hero", [""])[0])
            self.send_json({"ok": not err, "err": err, "at": at,
                            "cur": "%dx%d" % (core.SCREEN_W,
                                              core.SCREEN_H)})
            return

        if u.path == "/setfav":
            self.send_json({"ok": set_fav(q.get("name", [""])[0],
                                          q.get("on", ["1"])[0] != "0")})
            return

        if u.path == "/onlyfav":
            self.send_json({"ok": set_only_fav(
                q.get("on", ["1"])[0] != "0")})
            return

        if u.path == "/sethero":
            self.send_json({"ok": pick_hero(q.get("name", [""])[0])})
            return

        if u.path == "/retry":
            conf = conf_view()
            who = q.get("hero", [""])[0]
            ok = bool(who) and core.forget_result(conf.get("difficulty"), who)
            if ok:
                log("обход: %s возвращён в очередь" % who)
            self.send_json({"ok": ok})
            return

        if u.path == "/setmode":
            self.send_json({"ok": set_mode(q.get("name", [""])[0])})
            return

        if u.path == "/setdiff":
            try:
                num = int(q.get("n", ["0"])[0])
            except ValueError:
                num = 0
            self.send_json({"ok": set_difficulty(num)})
            return

        if u.path == "/setfn":
            key = q.get("name", [""])[0]
            who = q.get("hero", [""])[0]
            val = q.get("on", ["1"])[0] != "0"
            ok = bool(key) and set_func(who, key, val)
            self.send_json({"ok": ok, "on": val})
            return

        if u.path == "/hide":
            key = q.get("id", [""])[0]
            on = q.get("on", ["1"])[0] != "0"
            self.send_json({"ok": bool(key) and stats.hide(key, on)})
            return

        if u.path == "/stats":
            allv = bool(q.get("all"))
            self.page("статистика",
                      SUB_BODY % {"inner": stats_html(with_hidden=allv)},
                      HIDE_JS % {"k": TOKEN})
            return

        if u.path == "/log":
            name = os.path.basename(q.get("f", [""])[0] or
                                    os.path.basename(core.LOG_FILE))
            path = os.path.join(HERE, name)
            if not name.startswith("bot.log") or not os.path.exists(path):
                path, name = core.LOG_FILE, os.path.basename(core.LOG_FILE)
            whole = bool(q.get("all"))
            more = ("" if whole else
                    '<p class="dim"><a href="/log?k=%s&f=%s&all=1">'
                    'показать файл целиком</a></p>'
                    % (TOKEN, urllib.parse.quote(name)))
            inner = (log_choices(name) + more
                     + '<pre id="log">%s</pre>'
                     % html.escape(read_log(path, whole)))
            self.page("логи", SUB_BODY % {"inner": inner}, SCROLL_JS)
            return

        if u.path == "/tail":
            try:
                frm = int(q.get("from", ["0"])[0])
            except ValueError:
                frm = 0
            pos, add = log_since(frm)
            self.send_json(self.state(add=add, pos=pos))
            return

        if u.path == "/state":
            self.send_json(self.state())
            return

        note = ""
        if u.path == "/shot":
            note = "снимок сделан в %s" % take_shot()
        elif u.path == "/start":
            note = "бот " + bot.start()
        elif u.path == "/stop":
            note = "бот " + bot.stop()
        elif u.path == "/restart":
            bot.stop()
            note = "бот " + bot.start()
        elif u.path != "/":
            self.send("нет такой страницы", code=404)
            return

        if q.get("x"):
            self.send_json(self.state(note=note))
            return

        text, pos = home_log()
        stamp = shot_stamp()
        shot = (' src="/img?k=%s&t=%d"' % (TOKEN, stamp) if stamp
                else ' style="display:none"')
        body = HOME_BODY % {"shot": shot, "log": html.escape(text)}
        self.page("автоигра", body,
                  LIVE_JS % {"pos": pos, "shot": stamp, "k": TOKEN})


def lan_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        s.close()


def show(url, why):
    print("   %-18s%s" % (why, url))


def offer(url, why):
    show(url, why)
    if core.copy_clipboard(url):
        print("   %-18sскопирована в буфер обмена" % "")
    else:
        print("   %-18s(в буфер положить не удалось)" % "")


def main():
    threading.Thread(target=bot.watch, daemon=True).start()
    if "--run" in sys.argv:
        bot.start([a for a in sys.argv[1:] if a != "--run"])
    try:
        tunnel.start()
    except Exception as e:
        print("   туннель не поднят (%s: %s)"
              % (type(e).__name__, e))
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    home = "http://%s:%d/?k=%s" % (lan_ip(), PORT, TOKEN)
    print("\nПульт поднят. Бот выключен — включается кнопкой в шапке"
          " (сразу при запуске: py remote.py --run).")
    print("Открывай с телефона:")
    show(home, "в домашней сети:")
    if RCFG.get("hostname"):
        offer("https://%s/?k=%s" % (RCFG["hostname"], TOKEN), "снаружи:")
    elif tunnel.mode == "quick":
        print("   %-18sадрес появится, когда поднимется туннель" % "снаружи:")
    else:
        print("   %-18sтуннель выключен (remote.tunnel в конфиге),"
              % "снаружи:")
        print("   %-18sкопировать нечего" % "")
    print("\nБез токена страница не отдаётся. Ctrl+C — закрыть пульт и бота.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        bot.stop()
        tunnel.stop()
        srv.server_close()
        print("пульт закрыт")


if __name__ == "__main__":
    main()
