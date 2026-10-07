"""Утилиты ввода/вывода и взаимодействия с пользователем."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from .core import SUPPORTED_SUFFIXES

__all__ = [
    "get_user_input",
    "normalize_path",
    "find_kml_files",
    "resolve_input_path",
    "ask_file_path",
    "ask_folder_name",
    "ask_mode",
]

Validator = Callable[[str], bool]

# Кавычки, в которые путь оборачивают Windows («Копировать как путь»), консоль и текстовые редакторы
QUOTES = "\"'`«»“”„‘’"


def get_user_input(prompt: str, validator: Optional[Validator] = None) -> Optional[str]:
    """Безопасный ввод строки с поддержкой !cancel и !exit.

    Возвращает:
    - введённую строку (str), если она прошла валидацию;
    - None, если пользователь ввёл !cancel;
    - НЕ возвращает управление, если пользователь ввёл !exit или ввод закрыт (происходит выход).
    """
    while True:
        try:
            user_input = input(prompt).strip()
        except KeyboardInterrupt:
            print("\nОперация прервана. Для выхода введите !exit")
            continue
        except EOFError:
            # Поток ввода закрыт: повторный input() снова даст EOFError
            print("\nВвод завершен. Выход из программы.")
            sys.exit(0)

        if user_input == "!cancel":
            return None
        if user_input == "!exit":
            print("Выход из программы.")
            sys.exit(0)

        if validator is None or validator(user_input):
            return user_input

        print("Некорректный ввод. Попробуйте снова.")


def normalize_path(raw: str) -> Path:
    """Приводит введённую строку к пути.

    Убирает пробелы и кавычки по краям, раскрывает переменные окружения (%USERPROFILE%) и ~.
    """
    cleaned = raw.strip().strip(QUOTES).strip()
    return Path(os.path.expandvars(cleaned)).expanduser()


def find_kml_files(folder: Path) -> List[Path]:
    """Возвращает файлы .kml/.kmz из папки (без подпапок), по алфавиту."""
    return sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES),
        key=lambda p: p.name.lower(),
    )


def resolve_input_path(raw: str) -> Optional[Path]:
    """Превращает введённый путь в путь к KML/KMZ файлу.

    - путь к файлу .kml/.kmz возвращается как есть;
    - в папке ищутся подходящие файлы: единственный берётся сразу,
      из нескольких пользователь выбирает по номеру.

    Возвращает None, если файл получить не удалось (причина выводится на экран).
    """
    path = normalize_path(raw)

    try:
        if path.is_file():
            if path.suffix.lower() in SUPPORTED_SUFFIXES:
                return path
            print(f"Нужен файл .kml или .kmz, а указан: {path.name}")
            return None

        if not path.is_dir():
            print(f"Файл или папка не существует: {path}")
            return None

        files = find_kml_files(path)
        if not files:
            print(f"В папке нет файлов .kml или .kmz: {path}")
            return None

        if len(files) == 1:
            print(f"Найден файл: {files[0].name}")
            return files[0]

        print(f"В папке найдено файлов: {len(files)}")
        for i, file in enumerate(files, 1):
            stat = file.stat()
            modified = datetime.fromtimestamp(stat.st_mtime)
            print(f"  {i}. {file.name}  ({stat.st_size / 1024:.0f} КБ, {modified:%d.%m.%Y %H:%M})")

    except OSError as e:
        print(f"Не удалось прочитать путь: {e}")
        return None

    choice = get_user_input(
        "Введите номер файла (!cancel - указать другой путь): ",
        lambda x: x.isdecimal() and 1 <= int(x) <= len(files),
    )
    return files[int(choice) - 1] if choice is not None else None


def ask_file_path() -> Optional[Path]:
    """Запрашивает путь к файлу или папке, пока не получит KML/KMZ файл."""
    while True:
        raw = get_user_input("Введите путь к файлу или папке: ", lambda x: bool(x))
        if raw is None:
            print("Отмена ввода пути к файлу.")
            return None

        path = resolve_input_path(raw)
        if path is not None:
            return path


def ask_folder_name() -> Optional[str]:
    """Запрашивает имя папки.

    Возвращает None, если пользователь завершил набор папок (пустая строка или !cancel).
    """
    name = get_user_input("Введите имя папки (ENTER или !cancel - завершить и сохранить файл): ")
    return name or None


def ask_mode() -> Optional[str]:
    """Запрашивает режим ввода (1 — интерактивный, 2 — пакетный)."""
    mode = get_user_input(
        "Режим ввода (1 - интерактивный, 2 - пакетный): ",
        lambda x: x in ("1", "2"),
    )
    return mode
