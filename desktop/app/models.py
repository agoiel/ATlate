"""Базовые структуры данных: прямоугольники, строки текста, облака."""

from __future__ import annotations

from dataclasses import dataclass, field


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
class TextLine:
    """Одна строка текста, найденная OCR."""

    box: Box
    text: str
    conf: float = 1.0
    # наклон строки в градусах (плюс — правый край выше), если распознавание смогло его оценить
    angle: float | None = None


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
    # Подробности для режима разработчика
    debug: "DebugInfo | None" = None


# ---------------------------------------------------------------- режим разработчика

VARIANT_NAMES = {
    "orig": "оригинал", "inv": "инверсия", "local": "локальный контраст",
    "local_inv": "инверсия контраста", "color": "цветовой контраст",
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
        mode = f"параллельно ×{self.threads}" if self.threads > 1 else "по очереди"
        return f"Всего {self.total_ms:.0f} мс · {mode}"

    def report(self) -> str:
        out = [self.summary()]
        if self.stages:
            out.append(" · ".join(f"{n} {ms:.0f}" for n, ms in self.stages))
        if self.passes:
            out.append("Проходы:")
            for p in self.passes:
                err = " ⚠" if p.error else ""
                out.append(f"  {VARIANT_NAMES.get(p.variant, p.variant)}: {len(p.lines)} строк "
                           f"({p.ocr_ms:.0f} мс, подготовка {p.prep_ms:.0f} мс){err}")
        line = (f"Строк {self.lines} (проходы добавили {self.added}, заменили {self.replaced})"
                f" · облаков {self.bubbles}")
        if self.cache_hits:
            line += f" · из кэша {self.cache_hits}"
        out.append(line)
        if self.note:
            out.append(self.note)
        errors = sorted({p.error for p in self.passes if p.error})
        if errors:
            out.append("Ошибки проходов:")
            out.extend(errors)
        if self.error:
            out.append(f"Ошибка: {self.error}")
        return "\n".join(out)
