"""Пакет с логикой работы с KML/KMZ и CLI-интерфейсом.

Структура:
- core.py      — низкоуровневая работа с KML/KMZ (парсинг, поиск, перемещение).
- services.py  — сервисные функции уровня бизнес-логики.
- io_utils.py  — функции безопасного взаимодействия с пользователем и вводом.
- cli.py       — интерактивный консольный интерфейс и запуск сценариев.
"""

from .core import KmlDocumentManager
from .cli import main as run_cli_main

__all__ = ["KmlDocumentManager", "run_cli_main"]

