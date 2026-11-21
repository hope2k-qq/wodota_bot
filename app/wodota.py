"""
Один вход для всего: пульт, бот, проверки.

    WoDOTA.exe                 пульт, бот выключен
    WoDOTA.exe --run           пульт и сразу бот
    WoDOTA.exe --bot           только бот, без пульта
    WoDOTA.exe --bot --check   проверки бота
    WoDOTA.exe --party main    проверка связи с напарником
    WoDOTA.exe --checks        что ещё нужно настроить

То же самое без сборки: py app/wodota.py --bot.

Собранный exe запускает сам себя, когда пульту нужно поднять бота
дочерним процессом, поэтому вход один на всех.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ready():
    try:
        import PIL
    except ImportError:
        req = os.path.join(ROOT, "requirements.txt")
        print()
        print("Нет библиотеки Pillow — без неё бот не видит экран.")
        print("Поставь её той же версией Python, которой запускаешь:")
        print()
        print('    "%s" -m pip install -r "%s"' % (sys.executable, req))
        print()
        print("Если Python на машине несколько, проще запускать через")
        print("WoDOTA.cmd — он ставит сам.")
        print()
        return False
    if sys.version_info < (3, 8):
        print("Нужен Python 3.8 или новее, сейчас %d.%d"
              % sys.version_info[:2])
        return False
    return True


def main():
    args = sys.argv[1:]
    first = args[0] if args else ""

    if not ready():
        return 2

    if first == "--bot":
        sys.argv = [sys.argv[0]] + args[1:]
        import bot
        return bot.main()

    if first == "--party":
        import party
        role = args[1] if len(args) > 1 else ""
        if role not in ("main", "second"):
            print("роль: main или second")
            return 2
        return party.selftest(role)

    if first == "--machine":
        import machine
        machine.main()
        return 0

    if first == "--checks":
        import checks
        return checks.main()

    import remote
    remote.main()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
