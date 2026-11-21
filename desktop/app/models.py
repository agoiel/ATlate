"""Базовые структуры данных: прямоугольники, строки текста, облака."""

from __future__ import annotations

from dataclasses import dataclass, field

from .i18n import tr


@dataclass
class Box:
    """Прямоугольник в пикселях картинки (x, y — левый верхний угол)."""

    x: float
    y: float
    w: float
    h: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    def union(self, other: "Box") -> "Box":
        x, y = min(self.x, other.x), min(self.y, other.y)
        x2, y2 = max(self.x2, other.x2), max(self.y2, other.y2)
        return Box(x, y, x2 - x, y2 - y)

    def scaled(self, k: float) -> "Box":
        return Box(self.x * k, self.y * k, self.w * k, self.h * k)

    def to_dict(self) -> dict:
        return {"x": round(self.x, 1), "y": round(self.y, 1), "w": round(self.w, 1), "h": round(self.h, 1)}


@dataclass
class Word:
    """Слово строки и его рамка — для «Слов на экране» (наведение мыши, словарик)."""

    text: str
    box: Box


@dataclass
class TextLine:
    """Одна строка текста, найденная OCR."""

    box: Box
    text: str
    conf: float = 1.0
    # наклон строки в градусах (плюс — правый край выше), если распознавание смогло его оценить
    angle: float | None = None
    # слова строки с рамками (Windows OCR даёт их сам; у EasyOCR — пусто, см. words_of)
    words: list[Word] = field(default_factory=list)


def words_of(line: TextLine) -> list[Word]:
    """Слова строки. Если распознавание не дало рамок слов, строка делится по пробелам,
    а ширина — пропорционально числу букв (для наведения мыши этого достаточно)."""
    if line.words:
        return line.words
    parts = line.text.split()
    if not parts:
        return []
    total = sum(len(p) for p in parts) + (len(parts) - 1)
    unit = line.box.w / max(1, total)
    out, x = [], line.box.x
    for part in parts:
        w = unit * len(part)
        out.append(Word(part, Box(x, line.box.y, w, line.box.h)))
        x += w + unit
    return out


@dataclass
class Bubble:
    """Облако (блок текста), собранное из нескольких строк."""

    box: Box
    lines: list[TextLine]
    text: str = ""
    # bubble — однотонный светлый фон (обычное облако)
    # box    — однотонный тёмный/цветной фон (плашка рассказчика, системное окно)
    # free   — пёстрый фон (звуки, надписи поверх рисунка)
    kind: str = "bubble"
    bg_color: tuple[int, int, int] = (255, 255, 255)
    translation: str = ""
    # Язык облака, определённый по символам (ko, ja, zh, en …)
    lang: str = ""
    # Причина, по которой облако не переводится (пусто — переводится)
    skip_reason: str = ""
    # Наклон текста в градусах (плюс — правый край выше; 0 — ровно) и настоящие размеры
    # наклонённого текста: высота строки, длина и толщина всего блока, его центр
    angle: float = 0.0
    line_h: float = 0.0
    tilt_size: tuple[float, float] = (0.0, 0.0)
    tilt_center: tuple[float, float] = (0.0, 0.0)

    @property
    def translatable(self) -> bool:
        return not self.skip_reason and bool(self.text.strip())


@dataclass
class PageResult:
    """Результат обработки одного снимка."""

    bubbles: list[Bubble] = field(default_factory=list)
    image_size: tuple[int, int] = (0, 0)
    timings: dict[str, float] = field(default_factory=dict)
    error: str = ""
    cache_hits: int = 0
    # Код языка, для которого в Windows не хватает OCR (тогда предлагаем докачать)
    missing_ocr_lang: str = ""
    # Сколько строк нашло распознавание (до сборки в облака)
    line_count: int = 0
    # Автоопределение языка: какой язык выбран в этот раз (пусто — язык не менялся)
    detected_lang: str = ""
    # Язык, который определил сам переводчик (текст на латинице: «определит переводчик»), код ISO
    source_detected: str = ""
    # Сколько новых терминов добавил авто-глоссарий
    glossary_added: int = 0
    # Выбранный бесплатный переводчик не ответил и перевёл другой — что показать (пусто — не было)
    fallback: str = ""
    # Подробности для режима разработчика
    debug: "DebugInfo | None" = None

    def translations(self) -> list[str]:
        """Переводы облаков по порядку чтения (пустые пропускаются)."""
        return [b.translation.strip() for b in self.bubbles if b.translation and b.translation.strip()]

    def copy_text(self) -> str:
        """Перевод страницы для буфера обмена: облака по порядку, между ними пустая строка."""
        return "\n\n".join(self.translations())


# ---------------------------------------------------------------- режим разработчика

VARIANT_NAMES = {
    "orig": tr("оригинал"), "inv": tr("инверсия"), "local": tr("локальный контраст"),
    "local_inv": tr("инверсия контраста"), "color": tr("цветовой контраст"),
}

# Цвета рамок для проходов — по порядку вариантов
PASS_COLORS = [(80, 200, 120), (90, 160, 255), (255, 170, 60), (230, 90, 200), (250, 230, 80)]


@dataclass
class PassInfo:
    """Один проход распознавания: вариант картинки."""

    variant: str
    lines: list[TextLine]
    prep_ms: float = 0.0
    ocr_ms: float = 0.0
    error: str = ""


@dataclass
class DebugInfo:
    """Подробности одного запуска: время этапов, проходы, итог объединения."""

    passes: list[PassInfo] = field(default_factory=list)
    stages: list[tuple[str, float]] = field(default_factory=list)  # (этап, мс)
    total_ms: float = 0.0
    threads: int = 1
    added: int = 0
    replaced: int = 0
    lines: int = 0
    bubbles: int = 0
    cache_hits: int = 0
    error: str = ""
    note: str = ""      # дополнительная строка отчёта (например, оценки автоопределения языка)

    def summary(self) -> str:
        mode = tr("параллельно ×{0}", self.threads) if self.threads > 1 else tr("по очереди")
        return tr("Всего {0:.0f} мс · {1}", self.total_ms, mode)

    def report(self) -> str:
        out = [self.summary()]
        if self.stages:
            out.append(" · ".join(f"{n} {ms:.0f}" for n, ms in self.stages))
        if self.passes:
            out.append(tr("Проходы:"))
            for p in self.passes:
                err = " ⚠" if p.error else ""
                out.append(tr("  {0}: {1} строк ({2:.0f} мс, подготовка {3:.0f} мс){4}",
                              VARIANT_NAMES.get(p.variant, p.variant), len(p.lines), p.ocr_ms, p.prep_ms, err))
        line = (tr("Строк {0} (проходы добавили {1}, заменили {2}) · облаков {3}",
                   self.lines, self.added, self.replaced, self.bubbles))
        if self.cache_hits:
            line += tr(" · из кэша {0}", self.cache_hits)
        out.append(line)
        if self.note:
            out.append(self.note)
        errors = sorted({p.error for p in self.passes if p.error})
        if errors:
            out.append(tr("Ошибки проходов:"))
            out.extend(errors)
        if self.error:
            out.append(tr("Ошибка: {0}", self.error))
        return "\n".join(out)
