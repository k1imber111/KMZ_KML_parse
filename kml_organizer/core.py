"""Ядро работы с KML/KMZ: парсинг, поиск меток и построение экспортного документа."""

from __future__ import annotations

import codecs
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from copy import deepcopy
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from .logging_config import get_logger

__all__ = ["KmlDocumentManager", "SUPPORTED_SUFFIXES", "normalize_name"]

SUPPORTED_SUFFIXES = (".kml", ".kmz")
KML_NS = "http://www.opengis.net/kml/2.2"
# Общие элементы документа, на которые метки ссылаются через styleUrl/schemaUrl
SHARED_TAGS = ("Style", "StyleMap", "Schema")
MASK_CHARS = "*?["
# Символы, отделяющие номер метки от описания: 10_Machta, 10 Machta, 10-A, 10.1
NAME_SEPARATORS = "_ -."

# Префиксы, которые встречаются в файлах Google Earth
KNOWN_PREFIXES = {
    "gx": "http://www.google.com/kml/ext/2.2",
    "atom": "http://www.w3.org/2005/Atom",
    "kml": KML_NS,
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "xal": "urn:oasis:names:tc:ciq:xsdschema:xAL:2.0",
}

# ElementTree не хранит исходные префиксы — без регистрации в файле появятся ns0/ns1
for _prefix, _uri in KNOWN_PREFIXES.items():
    if _uri != KML_NS:
        ET.register_namespace(_prefix, _uri)

_CDATA = re.compile(r"(<!\[CDATA\[.*?\]\]>)", re.DOTALL)
_BARE_AMP = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")
_FIRST_TAG = re.compile(r"<(?![?!])[^\s>/]+")


def normalize_name(text: str) -> str:
    """Убирает пробелы по краям и схлопывает двойные, неразрывные пробелы и переносы."""
    return " ".join(text.split())


def _repair_xml(text: str) -> str:
    """Чинит частые ошибки разметки: «голый» & и необъявленные префиксы namespace."""
    parts = _CDATA.split(text)
    # Нечётные части — секции CDATA, в них & законен и трогать его нельзя
    parts[::2] = [_BARE_AMP.sub("&amp;", part) for part in parts[::2]]
    text = "".join(parts)

    missing = "".join(
        f' xmlns:{prefix}="{uri}"'
        for prefix, uri in KNOWN_PREFIXES.items()
        if re.search(rf"[<\s/]{prefix}:\w", text) and f"xmlns:{prefix}=" not in text
    )
    if missing:
        text = _FIRST_TAG.sub(lambda match: match.group(0) + missing, text, count=1)
    return text


class KmlDocumentManager:
    """Класс для управления KML/KMZ документом.

    Отвечает за:
    - загрузку и парсинг KML/KMZ;
    - поиск меток по именам, номерам и маскам;
    - построение нового документа с выбранными папками и метками.
    """

    def __init__(self, input_path: Path, logger: Optional[logging.Logger] = None) -> None:
        self.input_path = input_path
        self.root: Optional[ET.Element] = None
        self.namespace: str = ""
        self.placemarks: List[ET.Element] = []
        # Имя главного KML внутри исходного KMZ (None для обычного KML)
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
        """Возвращает нормализованное имя метки ('' — если имени нет)."""
        return normalize_name(pm.findtext(f"{self.namespace}name") or "")

    # ---------- Публичный интерфейс ----------

    def validate_input_file(self) -> bool:
        """Проверка существования и формата файла."""
        if not self.input_path.exists():
            self.logger.error("Файл не существует: %s", self.input_path)
            return False

        if not self.input_path.is_file():
            self.logger.error("Не является файлом: %s", self.input_path)
            return False

        if self.input_path.suffix.lower() not in SUPPORTED_SUFFIXES:
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
            # Ищем от корня: в файле может быть несколько Document или ни одного
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
        except (OSError, RuntimeError, NotImplementedError) as e:
            # RuntimeError/NotImplementedError — архив с паролем или неизвестным сжатием
            self.logger.error("Ошибка чтения файла: %s", e)
            return False

    # ---------- Загрузка и парсинг ----------

    @staticmethod
    def _main_kml_entry(names: List[str]) -> Optional[str]:
        """Выбирает главный KML архива: doc.kml в корне, иначе первый KML в корне, иначе любой."""
        kml_files = [name for name in names if name.lower().endswith(".kml")]
        root_level = [name for name in kml_files if "/" not in name]
        candidates = [name for name in root_level if name.lower() == "doc.kml"] + root_level + kml_files
        return candidates[0] if candidates else None

    def _load_raw_kml_bytes(self) -> Optional[bytes]:
        """Загружает исходный KML как bytes (из KML или внутри KMZ).

        Тип определяется по содержимому, а не по расширению: KMZ, сохранённый как .kml,
        остаётся архивом.
        """
        self.kml_entry = None

        if not zipfile.is_zipfile(self.input_path):
            return self.input_path.read_bytes()

        with zipfile.ZipFile(self.input_path, "r") as kmz:
            self.kml_entry = self._main_kml_entry(kmz.namelist())
            if self.kml_entry is None:
                self.logger.error("В KMZ архиве не найден KML файл")
                return None

            self.logger.info("Используется KML из KMZ: %s", self.kml_entry)
            return kmz.read(self.kml_entry)

    def _parse_xml_with_fallbacks(self, content: bytes) -> Optional[ET.Element]:
        """Пытается разобрать XML: как есть, с явной кодировкой, с починкой разметки.

        Декодирование строгое: потерянные буквы хуже, чем честная ошибка.
        """
        # Пустая строка или BOM перед <?xml ломают разбор
        if content.startswith(codecs.BOM_UTF8):
            content = content[len(codecs.BOM_UTF8):]
        content = content.lstrip()

        try:
            return ET.fromstring(content)
        except ET.ParseError as e:
            error = e
            self.logger.warning("Не удалось разобрать XML напрямую (%s), пробуем восстановить", e)

        # Файл мог быть сохранён не в той кодировке, что указана в его заголовке
        for encoding in ("utf-8", "cp1251"):
            try:
                text = content.decode(encoding)
            except UnicodeDecodeError:
                continue

            for candidate, repaired in ((text, False), (_repair_xml(text), True)):
                try:
                    root = ET.fromstring(candidate)
                except ET.ParseError:
                    continue
                if repaired:
                    self.logger.warning(
                        "В файле были ошибки разметки XML, они исправлены при чтении (кодировка %s)", encoding
                    )
                else:
                    self.logger.info("XML разобран в кодировке %s", encoding)
                return root

        self.logger.error("Ошибка парсинга XML: %s", error)
        return None

    # ---------- Поиск ----------

    def find_placemarks(self, patterns: List[str]) -> Tuple[List[ET.Element], List[str]]:
        """Поиск меток по списку запросов.

        Для каждого запроса по очереди, до первого результата:
        1. имя метки совпадает с запросом точно;
        2. имя начинается с запроса, а дальше идёт разделитель (10 -> 10_Machta);
        3. запрос содержит символы маски (* ? [) и применяется как маска.

        Возвращает:
        - список найденных элементов Placemark (без повторов);
        - список запросов, по которым ничего не нашли.
        """
        by_name: Dict[str, List[ET.Element]] = {}
        for pm in self.placemarks:
            name = self.name_of(pm)
            if name:
                by_name.setdefault(name, []).append(pm)

        found: Dict[ET.Element, None] = {}
        not_found: List[str] = []

        for pattern in dict.fromkeys(map(normalize_name, patterns)):
            if not pattern:
                continue

            if pattern in by_name:
                names = [pattern]
            else:
                # ponytail: перебор O(запросы x метки); индекс по началу имени, если станет медленно
                size = len(pattern)
                names = [
                    name
                    for name in by_name
                    if len(name) > size and name.startswith(pattern) and name[size] in NAME_SEPARATORS
                ]
                if not names and any(c in pattern for c in MASK_CHARS):
                    names = [name for name in by_name if fnmatchcase(name, pattern)]

            if not names:
                not_found.append(pattern)
            for name in names:
                found.update(dict.fromkeys(by_name[name]))

        return list(found), not_found

    # ---------- Экспорт в новый документ ----------

    def _shared_elements(self, parent: ET.Element) -> Iterator[ET.Element]:
        """Общие стили и схемы (с атрибутом id) вне меток, на любом уровне вложенности."""
        placemark_tag = f"{self.namespace}Placemark"
        shared = {f"{self.namespace}{tag}" for tag in SHARED_TAGS}

        for child in parent:
            if child.tag == placemark_tag:
                continue
            if child.tag in shared and child.get("id"):
                yield child
            else:
                yield from self._shared_elements(child)

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
        document.extend(deepcopy(element) for element in self._shared_elements(self.root))

        for folder_name, placemarks in session_folders.items():
            if not placemarks:
                continue

            folder_elem = ET.SubElement(document, f"{ns}Folder")
            ET.SubElement(folder_elem, f"{ns}name").text = folder_name
            folder_elem.extend(deepcopy(pm) for pm in placemarks)

        return ET.ElementTree(kml_root)

    def copy_resources(self, target: zipfile.ZipFile, export_root: ET.Element) -> None:
        """Переносит в целевой архив иконки и другие вложения.

        Для KMZ копируются все вложения исходного архива. Для KML — файлы рядом с исходником,
        на которые экспортный документ ссылается относительными href.
        """
        if self.kml_entry is not None:
            # ponytail: копируем все вложения, включая неиспользуемые; отбор по href — если архив раздуется
            with zipfile.ZipFile(self.input_path, "r") as source:
                for info in source.infolist():
                    if info.is_dir() or info.filename in (self.kml_entry, "doc.kml"):
                        continue
                    target.writestr(info, source.read(info))
            return

        base = self.input_path.parent.resolve()
        hrefs = {
            (element.text or "").strip()
            for element in export_root.iter()
            if element.tag.rsplit("}", 1)[-1] == "href"
        }

        for href in sorted(hrefs):
            if not href or "://" in href:
                continue
            try:
                local = (base / href).resolve()
            except (OSError, ValueError):
                continue
            if base not in local.parents:
                # Абсолютный путь или выход за пределы папки исходника — не наше
                continue

            if local.is_file():
                target.write(local, arcname=local.relative_to(base).as_posix())
            else:
                self.logger.warning("Иконка не найдена рядом с файлом: %s", href)
