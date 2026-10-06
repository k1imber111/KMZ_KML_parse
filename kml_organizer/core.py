"""Ядро работы с KML/KMZ: парсинг, поиск меток и построение экспортного документа."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
import zipfile
from copy import deepcopy
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .logging_config import get_logger

__all__ = ["KmlDocumentManager"]

KML_NS = "http://www.opengis.net/kml/2.2"
# Общие элементы документа, на которые метки ссылаются через styleUrl/schemaUrl
SHARED_TAGS = ("Style", "StyleMap", "Schema")
MASK_CHARS = "*?["

# ElementTree не хранит исходные префиксы — без регистрации в файле появятся ns0/ns1
ET.register_namespace("gx", "http://www.google.com/kml/ext/2.2")
ET.register_namespace("atom", "http://www.w3.org/2005/Atom")


class KmlDocumentManager:
    """Класс для управления KML/KMZ документом.

    Отвечает за:
    - загрузку и парсинг KML/KMZ;
    - поиск меток по именам и маскам;
    - построение нового документа с выбранными папками и метками.
    """

    def __init__(self, input_path: Path, logger: Optional[logging.Logger] = None) -> None:
        self.input_path = input_path
        self.root: Optional[ET.Element] = None
        self.namespace: str = ""
        self.placemarks: List[ET.Element] = []
        # Имя главного KML внутри исходного KMZ (None для .kml)
        self.kml_entry: Optional[str] = None
        self.logger = logger or get_logger(__name__)

    # ---------- Вспомогательные методы ----------

    def _detect_namespace(self) -> None:
        """Определяет namespace корневого элемента и кеширует его."""
        if self.root is None:
            self.namespace = ""
            return

        tag = self.root.tag
        if "}" in tag:
            self.namespace = tag.split("}", 1)[0] + "}"
        else:
            self.namespace = ""

    def name_of(self, pm: ET.Element) -> str:
        """Возвращает имя метки без пробелов по краям ('' — если имени нет)."""
        return (pm.findtext(f"{self.namespace}name") or "").strip()

    # ---------- Публичный интерфейс ----------

    def validate_input_file(self) -> bool:
        """Проверка существования и формата файла."""
        if not self.input_path.exists():
            self.logger.error("Файл не существует: %s", self.input_path)
            return False

        if not self.input_path.is_file():
            self.logger.error("Не является файлом: %s", self.input_path)
            return False

        if self.input_path.suffix.lower() not in (".kml", ".kmz"):
            self.logger.error("Формат файла должен быть .kml или .kmz")
            return False

        return True

    def parse_file(self) -> bool:
        """Парсинг KML/KMZ файла с обработкой ошибок и особенностей кодировок."""
        try:
            raw_bytes = self._load_raw_kml_bytes()
            if raw_bytes is None:
                return False

            root = self._parse_xml_with_fallbacks(raw_bytes)
            if root is None:
                return False

            self.root = root
            self._detect_namespace()
            # Ищем от корня: в файле может быть несколько Document
            self.placemarks = list(root.iter(f"{self.namespace}Placemark"))

            self.logger.info(
                "Файл успешно разобран: %s (меток: %d)",
                self.input_path,
                len(self.placemarks),
            )
            return True

        except zipfile.BadZipFile as e:
            self.logger.error("Некорректный KMZ архив: %s", e)
            return False
        except OSError as e:
            self.logger.error("Ошибка чтения файла: %s", e)
            return False

    # ---------- Загрузка и парсинг ----------

    def _load_raw_kml_bytes(self) -> Optional[bytes]:
        """Загружает исходный KML как bytes (из KML или внутри KMZ)."""
        suffix = self.input_path.suffix.lower()

        if suffix == ".kmz":
            with zipfile.ZipFile(self.input_path, "r") as kmz:
                kml_files = [name for name in kmz.namelist() if name.lower().endswith(".kml")]
                if not kml_files:
                    self.logger.error("В KMZ архиве не найден KML файл")
                    return None

                # Используем первый найденный KML
                self.kml_entry = kml_files[0]
                self.logger.info("Используется KML из KMZ: %s", self.kml_entry)
                return kmz.read(self.kml_entry)

        if suffix == ".kml":
            return self.input_path.read_bytes()

        self.logger.error("Неподдерживаемое расширение файла: %s", suffix)
        return None

    def _parse_xml_with_fallbacks(self, content: bytes) -> Optional[ET.Element]:
        """Пытается разобрать XML: сначала как есть, затем с явной кодировкой.

        Декодирование строгое: потерянные буквы хуже, чем честная ошибка.
        """
        try:
            return ET.fromstring(content)
        except ET.ParseError as e:
            error = e
            self.logger.warning("Не удалось распарсить XML напрямую (%s), пробуем другие кодировки", e)

        # Файл мог быть сохранён не в той кодировке, что указана в его заголовке
        for encoding in ("utf-8", "cp1251"):
            try:
                root = ET.fromstring(content.decode(encoding))
            except (UnicodeDecodeError, ET.ParseError):
                continue
            self.logger.info("XML разобран в кодировке %s", encoding)
            return root

        self.logger.error("Ошибка парсинга XML: %s", error)
        return None

    # ---------- Поиск ----------

    def find_placemarks(self, patterns: List[str]) -> Tuple[List[ET.Element], List[str]]:
        """Поиск меток по списку шаблонов.

        Шаблон сначала сравнивается с именем метки точно. Если точного совпадения нет
        и в шаблоне есть символы маски (* ? [), он применяется как маска.

        Возвращает:
        - список найденных элементов Placemark (без повторов);
        - список шаблонов, по которым ничего не нашли.
        """
        by_name: Dict[str, List[ET.Element]] = {}
        for pm in self.placemarks:
            name = self.name_of(pm)
            if name:
                by_name.setdefault(name, []).append(pm)

        found: Dict[ET.Element, None] = {}
        not_found: List[str] = []

        for pattern in dict.fromkeys(patterns):
            if pattern in by_name:
                names = [pattern]
            elif any(c in pattern for c in MASK_CHARS):
                names = [name for name in by_name if fnmatchcase(name, pattern)]
            else:
                names = []

            if not names:
                not_found.append(pattern)
            for name in names:
                found.update(dict.fromkeys(by_name[name]))

        return list(found), not_found

    # ---------- Экспорт в новый документ ----------

    def build_export_tree(self, session_folders: Dict[str, List[ET.Element]]) -> ET.ElementTree:
        """Строит новый KML-документ только с выбранными папками и метками.

        session_folders:
            ключ  — имя папки;
            значение — список исходных элементов Placemark, выбранных пользователем.
        """
        if not session_folders:
            raise ValueError("Нет данных для экспорта (session_folders пуст).")
        if self.root is None:
            raise RuntimeError("Документ не загружен")

        ns = self.namespace
        if ns:
            # Новые теги — в namespace исходника, иначе копии меток получат префикс ns0:
            ET.register_namespace("", ns[1:-1])
            kml_root = ET.Element(f"{ns}kml")
        else:
            kml_root = ET.Element("kml", xmlns=KML_NS)

        document = ET.SubElement(kml_root, f"{ns}Document")
        ET.SubElement(document, f"{ns}name").text = f"Exported from {self.input_path.name}"

        # ponytail: копируем все общие стили, включая неиспользуемые; отбор по styleUrl — если файл раздуется
        shared = {f"{ns}{tag}" for tag in SHARED_TAGS}
        for source_doc in self.root.iter(f"{ns}Document"):
            document.extend(deepcopy(child) for child in source_doc if child.tag in shared)

        for folder_name, placemarks in session_folders.items():
            if not placemarks:
                continue

            folder_elem = ET.SubElement(document, f"{ns}Folder")
            ET.SubElement(folder_elem, f"{ns}name").text = folder_name
            folder_elem.extend(deepcopy(pm) for pm in placemarks)

        return ET.ElementTree(kml_root)

    def copy_resources(self, target: zipfile.ZipFile) -> None:
        """Копирует вложения исходного KMZ (иконки и т.п.) в целевой архив.

        Для входного .kml ничего не делает.
        """
        if self.kml_entry is None:
            return

        # ponytail: копируем все вложения, включая неиспользуемые; отбор по href — если архив раздуется
        with zipfile.ZipFile(self.input_path, "r") as source:
            for info in source.infolist():
                if info.is_dir() or info.filename in (self.kml_entry, "doc.kml"):
                    continue
                target.writestr(info, source.read(info))
