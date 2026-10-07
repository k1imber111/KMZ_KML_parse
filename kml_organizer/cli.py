"""Интерактивный CLI-интерфейс для работы с KML/KMZ."""

from __future__ import annotations

import argparse
import itertools
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Sequence

from pathlib import Path

from . import services
from .io_utils import ask_file_path, ask_folder_name, ask_mode, get_user_input, resolve_input_path
from .logging_config import get_logger

__all__ = ["main", "run_menu", "parse_patterns", "setup_readline"]

YES_NO = ("y", "n", "д", "н", "")
NO = ("n", "н")


def setup_readline(placemark_names: List[str]) -> None:
    """Настройка автодополнения для readline (если доступен)."""
    try:
        import readline

        candidates = placemark_names + ["!cancel", "!exit"]

        def complete(text: str, state: int) -> Optional[str]:
            options = [name for name in candidates if name.startswith(text)]
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
    - точка с запятой: 10;11;12
    - смешанный вариант: 10:11,12;13

    Кавычки и фигурные скобки по краям названий отбрасываются —
    так строка из Excel вида {"10":"11"} разбирается без ручной чистки.
    """
    if not raw:
        return []

    parts = (p.strip(" \t\"'{}=") for p in re.split(r"[,:;]+", raw))
    return [p for p in parts if p]


def _free_output_path(input_path: Path) -> Path:
    """Возвращает свободное имя результата: <имя>_groups.kmz, затем _groups_2.kmz, _groups_3.kmz..."""
    base = input_path.with_name(f"{input_path.stem}_groups.kmz")
    numbered = (base.with_name(f"{base.stem}_{i}.kmz") for i in itertools.count(2))
    return next(path for path in itertools.chain([base], numbered) if not path.exists())


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
            if not assignments:
                break
            # ENTER легко нажать случайно — переспрашиваем, чтобы не оборвать работу
            finish = get_user_input("Завершить и сохранить файл? (Y/n): ", lambda x: x.lower() in YES_NO)
            if finish is None or finish.lower() in NO:
                continue
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
            batch_prompt = "Введите названия или номера меток через запятую, двоеточие или точку с запятой: "
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

        print(f"Запросов: {len(set(patterns))}, найдено меток: {len(found)}")
        for i, pm in enumerate(found[:10], 1):
            print(f"  {i}. {manager.name_of(pm)}")
        if len(found) > 10:
            print("... и еще", len(found) - 10, "меток")

        confirm_move = get_user_input("Подтвердите перемещение (y/N): ", lambda x: x.lower() in YES_NO)
        if confirm_move is None or confirm_move.lower() in (*NO, ""):
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

        continue_answer = get_user_input("Создать еще одну папку? (y/N): ", lambda x: x.lower() in YES_NO)
        if continue_answer is None or continue_answer.lower() in (*NO, ""):
            break

    if not assignments:
        logger.info("Не выбраны папки или метки для экспорта. Новый файл не создан.")
        return

    session_folders: Dict[str, List[ET.Element]] = {}
    for pm, name in assignments.items():
        session_folders.setdefault(name, []).append(pm)

    logger.info(
        "Итог: %s",
        "; ".join(f"'{name}' - {len(placemarks)}" for name, placemarks in session_folders.items()),
    )

    output_path = _free_output_path(input_path)
    if services.export_folders_to_kmz(manager, session_folders, output_path) is None:
        logger.error("Не удалось создать экспортированный KMZ файл.")


def _show_instructions() -> None:
    """Печатает инструкцию для пользователя."""
    print(
        "\n\tФайл\n"
        "Программа работает с файлами Google Планета Земля: kmz и kml\n"
        "Можно указать путь к файлу или к папке, где он лежит\n"
        "Если в папке несколько файлов, программа покажет список и попросит выбрать номер\n"
        "Скопировать полный путь: Зажать Shift + ПКМ на файл или папку - 'Копировать как путь'\n"
        "Путь вида: \"C:\\Папка\\Рабочий стол\\Метки.kmz\" - кавычки убирать не нужно\n"
        "\n\tПоиск меток\n"
        "1. Метка ищется по точному названию\n"
        "2. Если такой нет - по номеру в начале названия: 10 найдет 10_Machta, но не 100_Machta\n"
        "3. Допустимы маски: * - любые символы, ? - один символ. Пример: 10* найдет 10, 100, 10А\n"
        "Названия в строке разделяются запятой, двоеточием или точкой с запятой\n"
        "Каждая метка попадает только в одну папку - в ту, куда добавлена последней\n"
        "\n\tСохранение\n"
        "Чтобы завершить работу и сохранить файл, нажмите ENTER вместо имени папки\n"
        "Результат сохраняется рядом с исходным файлом: Метки_groups.kmz\n"
        "Если такой файл уже есть, он не перезаписывается: новый получит имя Метки_groups_2.kmz\n"
        "Для отмены операции введите !cancel, для выхода без сохранения - !exit\n"
        "\n\tExcel\n"
        "Преобразование вертикального списка в горизонтальный:\n"
        "В пустой ячейке поставьте «=», выделите мышью нужный диапазон со значениями вертикального списка и нажмите F9.\n"
        "Скопируйте появившуюся строку и вставьте в программу - знак «=», скобки и кавычки она уберет сама.\n"
    )
    get_user_input("Нажмите «ENTER» для продолжения: ")


def run_menu(initial_path: Optional[str] = None) -> None:
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

        input_path = resolve_input_path(initial_path) if initial_path else None
        initial_path = None
        if input_path is None:
            input_path = ask_file_path()
        if input_path is None:
            # Отмена ввода пути
            continue

        _interactive_session_for_file(input_path)


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(description="KML/KMZ Placemark Organizer")
    parser.add_argument("file_path", help="Путь к KML/KMZ файлу или к папке с ним", nargs="?")
    args = parser.parse_args(argv)

    run_menu(args.file_path)


if __name__ == "__main__":
    main()
