"""Настройка логирования для пакета kml_organizer."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional


def _ensure_log_dir(log_file: Path) -> None:
    """Гарантирует существование каталога для лог-файла."""
    try:
        if not log_file.parent.exists():
            log_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Логирование не должно ломать основную логику
        pass


def get_logger(name: Optional[str] = None, log_path: Optional[Path] = None) -> logging.Logger:
    """Возвращает настроенный логгер.

    - Логирование в stdout и в файл (по умолчанию kml_manager.log в текущей директории).
    - Конфигурация хэндлеров выполняется один раз.
    """
    logger_name = name or __name__
    logger = logging.getLogger(logger_name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )

    # Консоль
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Файл
    try:
        if log_path is None:
            log_path = Path("kml_manager.log")
        _ensure_log_dir(log_path)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        # Если не удалось создать файловый хэндлер, продолжаем только с консолью
        pass

    return logger

