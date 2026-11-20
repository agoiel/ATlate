"""Докачка компонентов Windows, которым нужны права администратора.

Сама программа работает без прав администратора. Когда не хватает OCR для языка,
запускается отдельный PowerShell с запросом прав (окно UAC «Разрешить изменения?»),
он устанавливает компонент и закрывается. Больше права не нужны.
"""

from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

from .i18n import tr
from .ocr.windows_ocr import capability_name

ERROR_CANCELLED = 1223          # пользователь нажал «Нет» в окне UAC
SEE_MASK_NOCLOSEPROCESS = 0x40
SW_SHOWNORMAL = 1
INFINITE = 0xFFFFFFFF


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def is_supported() -> bool:
    return sys.platform == "win32"


def _ps(text: str) -> str:
    """Строка для PowerShell в одинарных кавычках."""
    return "'" + text.replace("'", "''") + "'"


def _ps_install_script(langs: list[str]) -> str:
    names = ", ".join(f"'{capability_name(l)}'" for l in langs)
    title = _ps(tr("ATlate - установка OCR"))
    intro1 = _ps(tr("Устанавливаю распознавание текста для Windows."))
    intro2 = _ps(tr("Это может занять несколько минут. Не закрывайте окно."))
    failed = _ps(tr("Не получилось. Нажмите Enter, чтобы закрыть окно."))
    # имя пакета подставляет PowerShell: '...' + $n
    already, loading, done = _ps(tr("Уже установлено: ")), _ps(tr("Скачиваю: ")), _ps(tr("Готово: "))
    return f"""
$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = {title}
Write-Host ''
Write-Host {intro1} -ForegroundColor Cyan
Write-Host {intro2} -ForegroundColor Cyan
Write-Host ''
try {{
    foreach ($n in @({names})) {{
        $c = Get-WindowsCapability -Online -Name $n
        if ($c -and $c.State -eq 'Installed') {{
            Write-Host ({already} + $n) -ForegroundColor Green
            continue
        }}
        Write-Host ({loading} + $n + ' ...')
        Add-WindowsCapability -Online -Name $n | Out-Null
        Write-Host ({done} + $n) -ForegroundColor Green
    }}
    exit 0
}} catch {{
    Write-Host ''
    Write-Host $_ -ForegroundColor Red
    Write-Host ''
    Write-Host {failed}
    Read-Host | Out-Null
    exit 1
}}
"""


def install_ocr_languages(langs: list[str]) -> int | None:
    """Устанавливает OCR для языков с правами администратора и ждёт окончания.

    Возвращает код завершения (0 — успех) или None, если пользователь отказал в правах.
    Вызывать из фонового потока: функция ждёт, пока PowerShell не закончит.
    """
    script = _ps_install_script(langs)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    params = f"-NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}"

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    sei = _ShellExecuteInfo()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = "runas"
    sei.lpFile = "powershell.exe"
    sei.lpParameters = params
    sei.nShow = SW_SHOWNORMAL

    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        err = ctypes.get_last_error()
        if err == ERROR_CANCELLED:
            return None
        raise OSError(err, tr("Не удалось запустить установку (код {0})", err))
    if not sei.hProcess:
        return 0
    kernel32.WaitForSingleObject(sei.hProcess, INFINITE)
    code = wintypes.DWORD(1)
    kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(sei.hProcess)
    return int(code.value)


def missing_ocr_languages(langs: list[str]) -> list[str]:
    """Какие из языков ещё не установлены в Windows OCR."""
    from .ocr.windows_ocr import available_ocr_tags, find_ocr_tag
    available = available_ocr_tags()
    return [l for l in langs if find_ocr_tag(l, available) is None]
