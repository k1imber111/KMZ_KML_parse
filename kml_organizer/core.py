"""Ядро работы с KML/KMZ: парсинг, поиск и перемещение меток."""

from __future__ import annotations

import logging
from copy import deepcopy
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .logging_config import get_logger


class KmlDocumentManager:
    """Класс для управления KML/KMZ документом.

    Отвечает за:
    - загрузку и парсинг KML/KMZ;
    - поиск папок и меток;
    - создание папок и перемещение меток;
    - сохранение результата в файл.
    """

    def __init__(self, input_path: Path, logger: Optional[logging.Logger] = None) -> None:
        self.input_path = input_path
        self.root: Optional[ET.Element] = None
        self.namespace: str = ""
        self.placemarks: List[ET.Element] = []
        self.folders: Dict[str, ET.Element] = {}
        self.parent_map: Dict[ET.Element, ET.Element] = {}
        self.modified: bool = False
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

    def _build_parent_map(self) -> None:
        """Строит карту child -> parent для всего дерева."""
        self.parent_map.clear()
        if self.root is None:
            return

        for parent in self.root.iter():
            for child in list(parent):
                self.parent_map[child] = parent

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
            self._build_parent_map()
            self._extract_placemarks_and_folders()

            self.logger.info(
                "Файл успешно разобран: %s (меток: %d, папок: %d)",
                self.input_path,
                len(self.placemarks),
                len(self.folders),
            )
            return True

        except zipfile.BadZipFile as e:
            self.logger.error("Некорректный KMZ архив: %s", e)
            return False
        except ET.ParseError as e:
            self.logger.error("Ошибка парсинга XML: %s", e)
            return False
        except Exception as e:
            self.logger.error("Непредвиденная ошибка при чтении файла: %s", e, exc_info=True)
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
                kml_name = kml_files[0]
                self.logger.info("Используется KML из KMZ: %s", kml_name)
                with kmz.open(kml_name) as kml_file:
                    return kml_file.read()

        if suffix == ".kml":
            with self.input_path.open("rb") as f:
                return f.read()

        self.logger.error("Неподдерживаемое расширение файла: %s", suffix)
        return None

    def _parse_xml_with_fallbacks(self, content: bytes) -> Optional[ET.Element]:
        """Пытается разобрать XML, используя несколько стратегий."""
        # 1. Прямая попытка
        try:
            return ET.fromstring(content)
        except ET.ParseError:
            self.logger.warning("Не удалось распарсить XML напрямую, пробуем fallback-стратегии")

        # 2. Попытка как UTF-8
        try:
            text = content.decode("utf-8", errors="ignore")
            return ET.fromstring(text)
        except ET.ParseError:
            self.logger.warning("Не удалось распарсить как UTF-8, пробуем cp1251")

        # 3. Попытка как cp1251 (часто используется под Windows/русский текст)
        try:
            text = content.decode("cp1251", errors="ignore")
            return ET.fromstring(text)
        except ET.ParseError as e:
            self.logger.error("Не удалось разобрать XML даже с fallback-стратегиями: %s", e)
            return None

    # ---------- Извлечение структур ----------

    def _get_document_element(self) -> ET.Element:
        """Возвращает элемент Document, если он есть, иначе корень."""
        if self.root is None:
            raise RuntimeError("Документ не загружен")

        doc = self.root.find(f".//{self.namespace}Document") if self.namespace else self.root.find(".//Document")
        return doc if doc is not None else self.root

    def _extract_placemarks_and_folders(self) -> None:
        """Извлекает папки и метки из документа."""
        self.placemarks.clear()
        self.folders.clear()

        if self.root is None:
            return

        doc = self._get_document_element()

        # Папки
        folder_xpath = f".//{self.namespace}Folder" if self.namespace else ".//Folder"
        name_xpath = f"{self.namespace}name" if self.namespace else "name"

        for folder in doc.findall(folder_xpath):
            name_elem = folder.find(name_xpath)
            if name_elem is None:
                continue
            folder_name = (name_elem.text or "").strip()
            if not folder_name:
                continue
            # Последняя папка с таким именем побеждает — это разумно для CLI-сценария
            self.folders[folder_name] = folder

        # Метки
        placemark_xpath = f".//{self.namespace}Placemark" if self.namespace else ".//Placemark"
        self.placemarks = list(doc.findall(placemark_xpath))

    # ---------- Операции над структурой ----------

    def create_folder(self, name: str) -> Optional[ET.Element]:
        """Создаёт новую папку, если её ещё нет.

        Возвращает созданный элемент или существующий, если папка уже была.
        """
        if not name:
            self.logger.warning("Имя папки пустое")
            return None

        existing = self.folders.get(name)
        if existing is not None:
            self.logger.info("Папка '%s' уже существует, используем её", name)
            return existing

        if self.root is None:
            raise RuntimeError("Документ не загружен")

        folder_tag = f"{self.namespace}Folder" if self.namespace else "Folder"
        name_tag = f"{self.namespace}name" if self.namespace else "name"

        folder = ET.Element(folder_tag)
        name_elem = ET.SubElement(folder, name_tag)
        name_elem.text = name

        doc = self._get_document_element()
        doc.append(folder)

        self.folders[name] = folder
        self.parent_map[folder] = doc
        self.modified = True
        self.logger.info("Создана папка: %s", name)
        return folder

    def find_placemarks(self, patterns: List[str], use_regex: bool = False) -> Tuple[List[ET.Element], List[str]]:
        """Поиск меток по списку шаблонов.

        Возвращает:
        - список найденных элементов Placemark;
        - список шаблонов, по которым ничего не нашли.
        """
        import re

        found: List[ET.Element] = []
        not_found = patterns.copy()

        if not self.placemarks:
            return found, not_found

        name_xpath = f"{self.namespace}name" if self.namespace else "name"

        for pm in self.placemarks:
            name_elem = pm.find(name_xpath)
            if name_elem is None or not name_elem.text:
                continue
            pm_name = name_elem.text

            for pattern in patterns:
                if use_regex:
                    try:
                        if re.match(pattern, pm_name):
                            found.append(pm)
                            if pattern in not_found:
                                not_found.remove(pattern)
                    except re.error:
                        self.logger.warning("Некорректное регулярное выражение: %s", pattern)
                else:
                    if pattern == pm_name:
                        found.append(pm)
                        if pattern in not_found:
                            not_found.remove(pattern)
                        break

        return found, not_found

    def move_placemarks(self, placemarks: List[ET.Element], folder: ET.Element) -> int:
        """Перемещает указанные метки в заданную папку.

        Возвращает количество реально перемещённых меток.
        """
        if self.root is None:
            raise RuntimeError("Документ не загружен")

        moved_count = 0

        for pm in placemarks:
            parent = self.parent_map.get(pm)
            if parent is not None:
                try:
                    parent.remove(pm)
                except ValueError:
                    # Если элемент уже был удалён ранее — пропускаем
                    pass

            folder.append(pm)
            self.parent_map[pm] = folder
            moved_count += 1

        if moved_count:
            self.modified = True

        return moved_count

    # ---------- Сохранение ----------

    def save_to_file(self) -> Optional[Path]:
        """Сохраняет модифицированный документ в новый файл.

        Для входного .kmz создаётся новый KMZ с обновлённым doc.kml.
        Для .kml — новый KML-файл.
        """
        if not self.modified:
            self.logger.info("Изменения отсутствуют, сохранение не требуется")
            return None

        if self.root is None:
            self.logger.error("Невозможно сохранить: документ не загружен")
            return None

        output_path = self.input_path.parent / f"{self.input_path.stem}_edited{self.input_path.suffix}"

        try:
            # Гарантируем namespace по умолчанию
            if "}" not in self.root.tag:
                self.root.set("xmlns", "http://www.opengis.net/kml/2.2")

            tree = ET.ElementTree(self.root)

            if self.input_path.suffix.lower() == ".kmz":
                temp_kml = output_path.with_suffix(".kml")
                tree.write(temp_kml, encoding="utf-8", xml_declaration=True)

                with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as kmz:
                    kmz.write(temp_kml, arcname="doc.kml")

                try:
                    temp_kml.unlink()
                except OSError:
                    # Не критично, если временный файл не удалился
                    self.logger.warning("Не удалось удалить временный файл: %s", temp_kml)
            else:
                tree.write(output_path, encoding="utf-8", xml_declaration=True)

            self.logger.info("Файл сохранён: %s", output_path)
            return output_path

        except Exception as e:
            self.logger.error("Ошибка сохранения: %s", e, exc_info=True)
            return None


    # ---------- Экспорт в новый документ ----------

    def clone_placemark(self, pm: ET.Element) -> ET.Element:
        """Возвращает глубокую копию метки.

        Клонируется вся внутренняя структура Placemark.
        """
        return deepcopy(pm)

    def build_export_tree(self, session_folders: Dict[str, List[ET.Element]]) -> ET.ElementTree:
        """Строит новый KML-документ только с выбранными папками и метками.

        session_folders:
            ключ  — имя папки;
            значение — список исходных элементов Placemark, выбранных пользователем.
        """
        if not session_folders:
            raise ValueError("Нет данных для экспорта (session_folders пуст).")

        ns_uri = "http://www.opengis.net/kml/2.2"

        # Корневой элемент KML
        kml_root = ET.Element("kml", xmlns=ns_uri)

        # Документ
        document = ET.SubElement(kml_root, "Document")
        name_elem = ET.SubElement(document, "name")
        name_elem.text = f"Exported from {self.input_path.name}"

        for folder_name, placemarks in session_folders.items():
            if not placemarks:
                continue

            folder_elem = ET.SubElement(document, "Folder")
            folder_name_elem = ET.SubElement(folder_elem, "name")
            folder_name_elem.text = folder_name

            for pm in placemarks:
                cloned = self.clone_placemark(pm)
                folder_elem.append(cloned)

        return ET.ElementTree(kml_root)

