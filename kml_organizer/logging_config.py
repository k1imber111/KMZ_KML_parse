"""Настройка логирования для пакета kml_organizer."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

__all__ = ["get_logger"]

PACKAGE_LOGGER = "kml_organizer"


class _ConsoleFormatter(logging.Formatter):
    """Для консоли — только текст сообщения; трассировка ошибки остаётся в файле лога."""

    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage()


def get_logger(name: Optional[str] = None, log_path: Optional[Path] = None) -> logging.Logger:
    """Возвращает логгер пакета.

    - Хэндлеры настраиваются один раз на логгере пакета, дочерние логгеры пишут через него.
    - В консоль идёт только текст сообщения, в файл (по умолчанию kml_manager.log
      в текущей директории) — полная запись со временем и уровнем.
    """
    package_logger = logging.getLogger(PACKAGE_LOGGER)

    if not package_logger.handlers:
        package_logger.setLevel(logging.INFO)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(_ConsoleFormatter())
        package_logger.addHandler(console_handler)

        try:
            if log_path is None:
                log_path = Path("kml_manager.log")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
            )
            package_logger.addHandler(file_handler)
        except OSError:
            # Если не удалось создать файловый хэндлер, продолжаем только с консолью
            pass

    return logging.getLogger(name or PACKAGE_LOGGER)
