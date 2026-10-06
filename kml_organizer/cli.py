"""Интерактивный CLI-интерфейс для работы с KML/KMZ."""

from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Sequence

from pathlib import Path

from . import services
from .io_utils import ask_file_path, ask_folder_name, ask_mode, get_user_input
from .logging_config import get_logger

__all__ = ["main", "run_menu", "parse_patterns", "setup_readline"]


def setup_readline(placemark_names: List[str]) -> None:
    """Настройка автодополнения для readline (если доступен)."""
    try:
        import readline

        def complete(text: str, state: int) -> Optional[str]:
            options = [name for name in placemark_names if name.startswith(text)] + [
                "!cancel",
                "!exit",
            ]
            return options[state] if state < len(options) else None

        readline.set_completer(complete)
        readline.parse_and_bind("tab: complete")
    except ImportError:
        # На Windows модуль readline обычно недоступен — просто игнорируем.
        pass


def parse_patterns(raw: str) -> List[str]:
    """Разбирает пакетный ввод меток.

    Поддерживаются разделители:
    - запятая: 10,11,12
    - двоеточие: 10:11:12
    - смешанный вариант: 10:11,12:13
    """
    if not raw:
        return []

    parts = re.split(r"[,:]+", raw)
    return [p.strip() for p in parts if p.strip()]


def _interactive_session_for_file(input_path: Path) -> None:
    """Основной сценарий работы с одним файлом."""
    logger = get_logger(__name__)

    try:
        manager = services.load_document(input_path)
    except RuntimeError as e:
        logger.error("%s", e)
        return

    logger.info("Загружено меток: %d", len(manager.placemarks))

    setup_readline([name for name in map(manager.name_of, manager.placemarks) if name])

    # Метка -> папка. Метка живёт только в одной папке: побеждает последний выбор.
    assignments: Dict[ET.Element, str] = {}

    while True:
        folder_name = ask_folder_name()
        if folder_name is None:
            # Набор папок завершён — переходим к сохранению
            break

        mode = ask_mode()
        if mode is None:
            # Отмена выбора режима
            continue

        patterns: List[str] = []

        if mode == "1":
            print("Вводите названия меток по одной (пустая строка для завершения, !cancel - отменить папку):")
            while pattern := get_user_input("Метка: "):
                patterns.append(pattern)
            if pattern is None:
                continue
        else:
            batch_prompt = "Введите названия меток через запятую или двоеточие: "
            patterns_input = get_user_input(batch_prompt)

            if patterns_input is None:
                continue

            patterns = parse_patterns(patterns_input)

        if not patterns:
            logger.warning("Не введены метки для поиска")
            continue

        found, not_found = manager.find_placemarks(patterns)

        if not_found:
            logger.warning("Не найдены: %s", ", ".join(not_found))

        if not found:
            logger.warning("Метки не найдены")
            continue

        print(f"Найдено меток: {len(found)}")
        for i, pm in enumerate(found[:10], 1):
            print(f"  {i}. {manager.name_of(pm)}")
        if len(found) > 10:
            print("... и еще", len(found) - 10, "меток")

        confirm_move = get_user_input(
            "Подтвердите перемещение (y/N): ",
            lambda x: x.lower() in ("y", "n", "д", "н", ""),
        )
        if confirm_move is None or confirm_move.lower() in ("n", "н", ""):
            continue

        # Вместо реального перемещения в исходном документе
        # запоминаем выбор пользователя для последующего экспорта.
        added = moved = 0
        for pm in found:
            previous = assignments.pop(pm, None)
            if previous != folder_name:
                added += 1
                moved += previous is not None
            assignments[pm] = folder_name

        logger.info("Добавлено меток в папку '%s': %d", folder_name, added)
        if moved:
            logger.warning("Из них перенесено из других папок: %d", moved)

        continue_answer = get_user_input(
            "Создать еще одну папку? (y/N): ",
            lambda x: x.lower() in ("y", "n", "д", "н", ""),
        )
        if continue_answer is None or continue_answer.lower() in ("n", "н", ""):
            break

    if not assignments:
        logger.info("Не выбраны папки или метки для экспорта. Новый файл не создан.")
        return

    session_folders: Dict[str, List[ET.Element]] = {}
    for pm, name in assignments.items():
        session_folders.setdefault(name, []).append(pm)

    output_path = input_path.parent / f"{input_path.stem}_groups.kmz"

    if services.export_folders_to_kmz(manager, session_folders, output_path) is None:
        logger.error("Не удалось создать экспортированный KMZ файл.")


def _show_instructions() -> None:
    """Печатает инструкцию для пользователя."""
    print(
        "\n\tВажно!\n"
        "Для стабильной работы используйте kmz или kml файлы\n"
        "Скопировать полный путь к файлу: Зажать Shift + ПКМ на файл - 'Копировать как путь'\n"
        "Ссылка должна быть вида: C:\\Папка\\Рабочий стол\\Метки.kmz (можно в кавычках)\n"
        "Для отмены операции введите !cancel, для выхода без сохранения - !exit\n"
        "Чтобы завершить работу и сохранить файл, нажмите ENTER вместо имени папки\n"
        "\n\tПоиск меток\n"
        "Метка ищется по точному названию. Допустимы маски: * - любые символы, ? - один символ\n"
        "Пример: 10* найдет 10, 100, 10А; 1? найдет 10, 11, 1А\n"
        "Каждая метка попадает только в одну папку - в ту, куда добавлена последней\n"
        "\n\tExcel\n"
        "Преобразование вертикального списка в горизонтальный:\n"
        "В пустой ячейке поставьте «=», выделите мышью нужный диапазон со значениями вертикального списка и нажмите F9.\n"
        "В появившейся формуле удалите фигурные скобки, знак «=» и знак «+».\n"
        "Затем через поиск и замену (Ctrl+H) замените двоеточие «:» или запятую «,» на нужный вам разделитель.\n"
    )
    get_user_input("Нажмите «ENTER» для продолжения: ")


def run_menu(initial_path: Optional[Path] = None) -> None:
    """Главное меню: выбор действия пользователем.

    initial_path — путь из аргумента командной строки; используется только для первого запуска.
    """
    while True:
        print(
            "Автор: Журавлев И.В. АО 'Международный аэропорт Сочи'\n"
            "ТГ: @Sochi10\n"
            f"{'-'*53}\n\n"
            "Выберите действие:\n"
            "Запустить программу - нажмите: 1\n"
            "Читать инструкцию - нажмите: 2\n"
            "Выйти - введите: !exit"
        )
        choice = get_user_input("Введите свой ответ: ", lambda x: x in ("1", "2"))
        if choice is None:
            # Пользователь прервал ввод, возвращаемся к началу цикла
            continue

        if choice == "2":
            _show_instructions()
            continue

        input_path = initial_path or ask_file_path()
        initial_path = None
        if input_path is None:
            # Отмена ввода пути
            continue

        _interactive_session_for_file(input_path)


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(description="KML/KMZ Placemark Organizer")
    parser.add_argument("file_path", help="Путь к KML/KMZ файлу", nargs="?")
    args = parser.parse_args(argv)

    run_menu(Path(args.file_path) if args.file_path else None)


if __name__ == "__main__":
    main()
