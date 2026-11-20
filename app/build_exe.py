"""
Собрать WoDOTA.exe — запуск без консоли и без установленного Python.

    py -m pip install pyinstaller
    py build_exe.py

Получается один файл `WoDOTA.exe` рядом с проектом. Настройки, билды,
координаты и справочники в него НЕ зашиваются: exe читает их из папки,
где лежит сам. Так их можно править и дальше — и руками, и с пульта.

Запускается тем же exe и бот: пульту нужно поднимать его дочерним
процессом, и он зовёт себя с `--bot`. Отдельного файла для бота нет.
"""

import os
import shutil
import subprocess
import sys

CODE = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.dirname(CODE)
NAME = "WoDOTA"

HIDDEN = ["bot", "remote", "core", "party", "machine", "stats",
          "restyle_builds", "make_builds"]


def main():
    try:
        import PyInstaller
    except ImportError:
        print("Нет PyInstaller. Поставь его и запусти снова:")
        print("    py -m pip install pyinstaller")
        return 1

    cmd = [sys.executable, "-m", "PyInstaller",
           "--onefile",
           "--name", NAME,
           "--distpath", HERE,
           "--workpath", os.path.join(HERE, "build"),
           "--specpath", os.path.join(HERE, "build"),
           "--console",
           "--noconfirm"]
    for mod in HIDDEN:
        cmd += ["--hidden-import", mod]
    cmd.append(os.path.join(CODE, "wodota.py"))

    print("Собираю %s.exe, это пара минут…" % NAME)
    r = subprocess.run(cmd)
    if r.returncode:
        print("Сборка не вышла, код %d" % r.returncode)
        return r.returncode

    exe = os.path.join(HERE, NAME + ".exe")
    size = os.path.getsize(exe) / 1024 / 1024 if os.path.exists(exe) else 0
    print()
    print("Готово: %s (%.0f МБ)" % (exe, size))
    print("Запускать из этой же папки — настройки и билды он берёт рядом.")
    shutil.rmtree(os.path.join(HERE, "build"), ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
