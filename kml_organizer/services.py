"""Сервисный слой над KmlDocumentManager."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .core import KmlDocumentManager
from .logging_config import get_logger


def load_document(path: Path) -> KmlDocumentManager:
    """Создаёт и инициализирует KmlDocumentManager для указанного файла.

    Выбрасывает RuntimeError при ошибках валидации или парсинга.
    """
    logger = get_logger(__name__)
    manager = KmlDocumentManager(path, logger=logger)

    if not manager.validate_input_file():
        raise RuntimeError(f"Файл не прошел валидацию: {path}")

    if not manager.parse_file():
        raise RuntimeError(f"Не удалось разобрать файл: {path}")

    if not manager.placemarks:
        raise RuntimeError("В файле не найдено меток для обработки.")

    return manager


def get_or_create_folder(manager: KmlDocumentManager, folder_name: str):
    """Возвращает существующую или создаёт новую папку."""
    return manager.create_folder(folder_name)


def find_placemarks(
    manager: KmlDocumentManager,
    patterns: List[str],
    use_regex: bool,
) -> Tuple[List[object], List[str]]:
    """Обёртка над методом поиска меток."""
    return manager.find_placemarks(patterns, use_regex)


def move_to_folder(
    manager: KmlDocumentManager,
    placemarks: List[object],
    folder,
) -> int:
    """Перемещает указанные метки в папку."""
    return manager.move_placemarks(placemarks, folder)


def save_if_modified(manager: KmlDocumentManager) -> Optional[Path]:
    """Сохраняет файл, если есть изменения."""
    return manager.save_to_file()


def export_folders_to_kmz(
    manager: KmlDocumentManager,
    session_folders: Dict[str, List[object]],
    output_path: Path,
) -> Optional[Path]:
    """Экспортирует выбранные папки и метки в отдельный KMZ-файл.

    Исходный документ на диске не изменяется.
    """
    logger = get_logger(__name__)

    if not session_folders:
        logger.info("Нет данных для экспорта (session_folders пуст).")
        return None

    try:
        tree = manager.build_export_tree(session_folders)  # type: ignore[arg-type]
    except Exception as e:
        logger.error("Не удалось построить экспортный KML-документ: %s", e, exc_info=True)
        return None

    temp_kml = output_path.with_suffix(".kml")

    try:
        tree.write(temp_kml, encoding="utf-8", xml_declaration=True)

        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as kmz:
            kmz.write(temp_kml, arcname="doc.kml")

        try:
            os.remove(temp_kml)
        except OSError:
            logger.warning("Не удалось удалить временный файл: %s", temp_kml)

        logger.info("Экспортированный KMZ файл сохранён: %s", output_path)
        return output_path

    except Exception as e:
        logger.error("Ошибка при экспорте KMZ: %s", e, exc_info=True)
        try:
            if temp_kml.exists():
                os.remove(temp_kml)
        except OSError:
            logger.warning("Не удалось удалить временный файл после ошибки: %s", temp_kml)
        return None


