"""Утилиты ввода/вывода и взаимодействия с пользователем."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional

__all__ = ["get_user_input", "ask_file_path", "ask_folder_name", "ask_mode"]

Validator = Callable[[str], bool]


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


def ask_file_path() -> Optional[Path]:
    """Запрашивает у пользователя путь к существующему файлу."""
    while True:
        file_path_str = get_user_input("Введите полный путь к файлу: ", lambda x: bool(x))
        if file_path_str is None:
            print("Отмена ввода пути к файлу.")
            return None

        # «Копировать как путь» в Windows оборачивает путь в кавычки
        path = Path(file_path_str.strip('"'))
        if path.exists() and path.is_file():
            return path

        print("Файл не существует или путь неверный. Попробуйте снова.")


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
