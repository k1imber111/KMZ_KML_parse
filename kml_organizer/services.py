"""Сервисный слой над KmlDocumentManager."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from .core import KmlDocumentManager
from .logging_config import get_logger

__all__ = ["load_document", "export_folders_to_kmz"]


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
        if any(element.tag.endswith("NetworkLink") for element in manager.root.iter()):
            raise RuntimeError(
                "В файле нет меток: он содержит только ссылку на внешнюю карту (NetworkLink). "
                "Откройте его в Google Планета Земля и сохраните через «Сохранить место как»."
            )
        raise RuntimeError("В файле не найдено меток для обработки.")

    return manager


def export_folders_to_kmz(
    manager: KmlDocumentManager,
    session_folders: Dict[str, List[ET.Element]],
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
        tree = manager.build_export_tree(session_folders)
        doc_kml = ET.tostring(tree.getroot(), encoding="utf-8", xml_declaration=True)

        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as kmz:
            kmz.writestr("doc.kml", doc_kml)
            manager.copy_resources(kmz, tree.getroot())

    except (OSError, zipfile.BadZipFile, ValueError, RuntimeError) as e:
        logger.error("Ошибка при экспорте KMZ: %s", e, exc_info=True)
        # Недописанный архив хуже отсутствующего
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Не удалось удалить недописанный файл: %s", output_path)
        return None

    logger.info("Экспортированный KMZ файл сохранён: %s", output_path)
    return output_path
