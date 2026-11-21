# -*- coding: utf-8 -*-
"""Игра в пати: два бота на двух ПК в одной сети.

Один ПК — мейн: он зовёт напарника и жмёт поиск. Второй — младший: он
ждёт приглашения, принимает его и дальше играет свой билд по своим
настройкам. Билды, таланты, закуп — у каждого свои: этот файл отвечает
только за то, чтобы два бота шли по лобби в ногу.

Связь — UDP на всю подсеть. Адрес напарника прописывать не надо: оба
кричат в сеть раз в секунду и так находят друг друга. В пакете лежит
пароль пати — он свой, не от пульта, и на двух ПК должен совпадать.

Проверить без Dota можно двумя окнами:

    python -X utf8 party.py main
    python -X utf8 party.py second

Оба пройдут сбор пати два круга на подставных нажатиях и напечатают, чем
кончилось.
"""

import json
import os
import secrets
import socket
import sys
import threading
import time

import core
from core import log

STATE_FILE = core.file("party_state.json")

MAGIC = "wodota-party"
VERSION = 1

STAGES = ("lobby", "invited", "joined", "go")


def conf_of(cfg):
    return (cfg or {}).get("party") or {}


class Link:

    def __init__(self, conf, hero=""):
        self.on = bool(conf.get("on"))
        self.role = (conf.get("role") or "main").lower().strip()
        self.port = int(conf.get("port") or 8742)
        self.token = str(conf.get("token") or "")
        self.name = conf.get("name") or socket.gethostname()
        self.hero = hero
        self.beat_s = float(conf.get("beat_s") or 1.0)
        self.stale_s = float(conf.get("stale_s") or 6.0)
        self.casts = list(conf.get("broadcast") or ["255.255.255.255"])
        self.id = secrets.token_hex(4)

        self.cycle = 0
        self.stage = ""
        self.marks = []
        self.together = False

        self.peer = {}
        self.peer_at = 0.0
        self.peer_cycle = None
        self.peer_seen = {}

        self.lock = threading.RLock()
        self.stop_flag = threading.Event()
        self.sock = None
        self.said = {}
        self.saved_at = 0.0
        self.saved = ""


    def start(self):
        if not self.on:
            return False
        if self.role not in ("main", "second"):
            log("пати: роль %r непонятна — жду main или second" % self.role)
            self.on = False
            return False
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind(("", self.port))
            s.settimeout(0.5)
        except OSError as e:
            log("пати: порт %d не открылся (%s: %s) — играю соло"
                % (self.port, type(e).__name__, e))
            self.on = False
            return False
        self.sock = s
        threading.Thread(target=self._listen, daemon=True).start()
        threading.Thread(target=self._beat, daemon=True).start()
        log("пати: роль %s, порт %d, пароль %s, зову по %s"
            % (self.role, self.port, "задан" if self.token else "НЕ ЗАДАН",
               ", ".join(self.casts)))
        return True

    def stop(self):
        self.stop_flag.set()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass


    def packet(self):
        with self.lock:
            return {"m": MAGIC, "v": VERSION, "id": self.id,
                    "token": self.token, "role": self.role, "name": self.name,
                    "hero": self.hero, "cycle": self.cycle,
                    "stage": self.stage, "marks": list(self.marks),
                    "ts": time.time()}

    def send(self):
        if not self.sock:
            return
        raw = json.dumps(self.packet()).encode("utf-8")
        for host in self.casts:
            try:
                self.sock.sendto(raw, (host, self.port))
            except OSError:
                pass

    def _beat(self):
        while not self.stop_flag.is_set():
            self.send()
            self.save()
            self.stop_flag.wait(self.beat_s)

    def _listen(self):
        while not self.stop_flag.is_set():
            try:
                raw, _ = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                pkt = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue
            self._take(pkt)

    def once(self, key, msg):
        if not self.said.get(key):
            self.said[key] = True
            log(msg)

    def _take(self, pkt):
        if pkt.get("m") != MAGIC or pkt.get("id") == self.id:
            return
        if str(pkt.get("token") or "") != self.token:
            self.once("token", "пати: в сети бот с другим паролем (%s) — "
                               "не слушаю его" % pkt.get("name", "?"))
            return
        if (pkt.get("role") or "") == self.role:
            self.once("role", "пати: у напарника (%s) та же роль %s — "
                              "на одном ПК поставь другую"
                      % (pkt.get("name", "?"), self.role))
            return
        with self.lock:
            fresh = not self.peer
            if pkt.get("cycle") != self.peer_cycle:
                self.peer_cycle = pkt.get("cycle")
                self.peer_seen = {}
            now = time.time()
            for m in pkt.get("marks") or []:
                self.peer_seen.setdefault(m, now)
            self.peer = pkt
            self.peer_at = now
        if fresh:
            log("пати: напарник на связи — %s, роль %s, билд %s"
                % (pkt.get("name", "?"), pkt.get("role", "?"),
                   pkt.get("hero") or "?"))


    def new_cycle(self):
        with self.lock:
            self.cycle += 1
            self.marks = []
            self.stage = ""
        self.send()

    def say(self, stage):
        with self.lock:
            self.stage = stage
            if stage not in self.marks:
                self.marks.append(stage)
        self.send()

    def saw(self, stage):
        with self.lock:
            return stage in self.peer_seen

    def alive(self):
        return bool(self.peer) and (time.time() - self.peer_at) <= self.stale_s

    def wait_any(self, stages, timeout, what, tick=None):
        start = time.time()
        told = False
        while time.time() - start < timeout:
            if tick:
                tick()
            with self.lock:
                for s in stages:
                    if s in self.peer_seen:
                        return s
            if not told and time.time() - start > 3:
                told = True
                log("пати: жду %s%s"
                    % (what, "" if self.alive() else " (напарника не слышно)"))
            time.sleep(0.2)
        log("пати: не дождался %s за %d с" % (what, round(timeout)))
        return ""

    def wait(self, stage, timeout, what, tick=None):
        return bool(self.wait_any((stage,), timeout, what, tick))


    def state(self):
        with self.lock:
            peer = dict(self.peer)
            return {
                "on": self.on, "role": self.role, "name": self.name,
                "hero": self.hero, "cycle": self.cycle, "stage": self.stage,
                "marks": list(self.marks), "together": self.together,
                "alive": self.alive(),
                "peer_age": round(time.time() - self.peer_at, 1)
                            if self.peer_at else None,
                "peer": {"name": peer.get("name"), "role": peer.get("role"),
                         "hero": peer.get("hero"), "stage": peer.get("stage"),
                         "marks": peer.get("marks") or [],
                         "cycle": peer.get("cycle")} if peer else None,
                "at": time.strftime("%H:%M:%S"),
            }

    def save(self, path=STATE_FILE):
        try:
            data = self.state()
            mark = json.dumps({k: v for k, v in data.items() if k != "at"},
                              sort_keys=True, ensure_ascii=False)
            now = time.time()
            if mark == self.saved and now - self.saved_at < 5:
                return
            self.saved, self.saved_at = mark, now
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except OSError:
            pass


def read_state(path=STATE_FILE):
    return core.read_json(path) or {}




def handshake(link, conf, invite, accept, tick=None):
    wait_s = float(conf.get("wait_s") or 300)
    accept_s = float(conf.get("accept_wait_s") or 90)
    together_press = bool(conf.get("press_together", True))

    link.new_cycle()
    link.say("lobby")

    if link.role == "second":
        got = link.wait_any(("invited", "go"), wait_s, "мейна в лобби", tick)
        if not got:
            log("пати: мейн не позвал — играю этот матч соло")
            return False, True
        if link.saw("invited"):
            if not accept():
                return False, True
            link.together = True
        link.say("joined")
        if not link.wait("go", accept_s, "мейна на поиске", tick):
            return link.together, True
        press = together_press and link.cycle > 1
        log("пати: идём вместе%s" % ("" if press else " — поиск жмёт мейн"))
        return True, press

    if not link.wait("lobby", wait_s, "напарника в лобби", tick):
        log("пати: напарник не вышел в лобби — играю этот матч соло")
        return False, True
    if not link.together or conf.get("invite_each_match"):
        if not invite():
            return False, True
        link.say("invited")
        if not link.wait("joined", accept_s, "приглашение принятым", tick):
            log("пати: напарник не принял приглашение — играю соло")
            return False, True
        link.together = True
    link.say("go")
    log("пати: вместе — жму поиск")
    return True, True




def selftest(role):
    cfg = core.load_config()
    conf = dict(conf_of(cfg))
    conf.update({"on": True, "role": role})
    link = Link(conf, hero="проверка-%s" % role)
    if not link.start():
        return 1

    def invite():
        print("  [мейн] вписал id %r и нажал «пригласить»"
              % (conf.get("invite_id") or ""))
        time.sleep(1)
        return True

    def accept():
        print("  [второй] дождался окна и нажал «принять»")
        time.sleep(1)
        return True

    try:
        for n in (1, 2):
            print("круг %d: жду напарника…" % n)
            together, press = handshake(link, conf, invite, accept)
            print("круг %d: вместе=%s, жать поиск=%s" % (n, together, press))
            if not together:
                print("  разошлись — проверь пароль, порт и брандмауэр")
            time.sleep(3)
    except KeyboardInterrupt:
        print("остановлено")
    finally:
        link.stop()
    return 0


if __name__ == "__main__":
    arg = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if arg in ("main", "second"):
        sys.exit(selftest(arg))
    print(__doc__)
    sys.exit(2)
