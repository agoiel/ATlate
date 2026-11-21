"""Описание релизов на GitHub: «Что нового» из release-notes/<тег>.md и инструкция по установке.

    python .github/scripts/release_notes.py compose v0.6.7      — напечатать описание нового релиза
    python .github/scripts/release_notes.py apply [тег …]       — обновить описания уже вышедших релизов
                                                                    (нужны gh и GH_TOKEN; без тегов — все,
                                                                    для которых есть заметки)

Инструкция по установке у вышедшего релиза сохраняется как есть (в старых релизах файлы назывались
agolate.apk / agolate-windows.zip); если её нет — подставляется обычная.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTES = ROOT / "release-notes"
INTRO = "Переводчик манхвы, манги и маньхуа прямо с экрана."
INSTALL = """## Android
Скачайте **ATlate.apk**, откройте его на телефоне и разрешите установку из этого источника.
Если Play Защита предупредит о неизвестном приложении, нажмите «Подробнее» → «Всё равно установить».
Нужны Android 8.0 или новее и сервисы Google Play.

## Windows
Скачайте **ATlate-windows.zip**, распакуйте и запустите `ATlate.exe`. Python не нужен.
Если Windows покажет «Система Windows защитила ваш компьютер», нажмите «Подробнее» → «Выполнить в любом случае».
Нужна Windows 10 или 11.

Инструкция и описание возможностей — в [README](https://github.com/agoiel/ATlate#readme)."""


def notes_for(tag: str) -> str:
    p = NOTES / f"{tag}.md"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def install_from(body: str) -> str:
    """Инструкция по установке из описания релиза: от «## Android» до строки про README."""
    body = body.replace("\r\n", "\n")
    start = body.find("## Android")
    if start < 0:
        return INSTALL
    m = re.search(r"^Инструкция и описание возможностей.*$", body[start:], re.M)
    end = start + m.end() if m else len(body)
    return body[start:end].strip()


def compose(tag: str, current: str = "") -> str:
    notes = notes_for(tag)
    parts = [INTRO]
    if notes:
        parts.append(f"## Что нового в {tag.lstrip('v')}\n\n{notes}")
    parts.append(install_from(current) if current else INSTALL)
    return "\n\n".join(parts) + "\n"


def gh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")


def apply(tags: list[str]) -> int:
    tags = tags or sorted((p.stem for p in NOTES.glob("v*.md")),
                          key=lambda t: [int(x) for x in re.findall(r"\d+", t)])
    failed = 0
    for tag in tags:
        r = gh("release", "view", tag, "--json", "body", "--jq", ".body")
        if r.returncode != 0:
            print(f"{tag}: релиза нет — пропускаю ({r.stderr.strip()[:120]})")
            continue
        current = r.stdout.rstrip("\n")
        new = compose(tag, current)
        if new.strip() == current.replace("\r\n", "\n").strip():
            print(f"{tag}: без изменений")
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(new)
        r = gh("release", "edit", tag, "--notes-file", f.name)
        if r.returncode != 0:
            failed += 1
            print(f"{tag}: не удалось обновить — {r.stderr.strip()[:200]}")
        else:
            print(f"{tag}: описание обновлено")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "compose":
        sys.stdout.write(compose(argv[1]))
        return 0
    if argv and argv[0] == "apply":
        return apply(argv[1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
