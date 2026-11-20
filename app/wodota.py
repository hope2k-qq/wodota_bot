"""
Один вход для собранного exe: пульт или бот, смотря по аргументу.

    WoDOTA.exe                 пульт, бот выключен
    WoDOTA.exe --run           пульт и сразу бот
    WoDOTA.exe --bot           только бот, без пульта
    WoDOTA.exe --bot --check   проверки бота
    WoDOTA.exe --party main    проверка связи с напарником

То же самое работает и без сборки: py wodota.py --bot.

Зачем вообще один файл: собранный exe запускает сам себя, когда пульту
нужно поднять бота дочерним процессом. Отдельного bot.exe для этого не
надо — хватает аргумента.
"""

import sys


def main():
    args = sys.argv[1:]
    first = args[0] if args else ""

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

    import remote
    remote.main()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
