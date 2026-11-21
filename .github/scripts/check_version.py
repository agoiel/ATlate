"""Номер версии в коде совпадает с тегом релиза.

    python .github/scripts/check_version.py v0.6.7

Релиз собирается из кода по тегу. Если перед релизом забыли поднять номер версии, программа
показывает старый номер (так было с 0.6.4 и 0.6.5 — в окне стояло 0.6.3), а проверка обновлений
предлагает «обновиться» на ту же версию. Код возврата 1, если номер не совпадает.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLACES = {
    "desktop/app/__init__.py": r'^__version__\s*=\s*"([^"]+)"',
    "android/app/build.gradle.kts": r'^\s*versionName\s*=\s*"([^"]+)"',
}


def versions() -> dict[str, str]:
    out = {}
    for rel, pattern in PLACES.items():
        m = re.search(pattern, (ROOT / rel).read_text(encoding="utf-8"), re.M)
        out[rel] = m.group(1) if m else "?"
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    want = argv[0].strip().lstrip("v")
    found = versions()
    bad = {f: v for f, v in found.items() if v != want}
    for f, v in found.items():
        print(f"{'OK  ' if v == want else 'FAIL'} {f}: {v}")
    if bad:
        print(f"\nТег v{want}, а в коде другой номер версии — сборка остановлена, файлы к релизу не прикреплены.\n"
              f"Поднимите номер в файлах выше, удалите релиз и тег v{want} и опубликуйте релиз заново "
              f"на исправленном коммите.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
