"""Интерактивный CLI-интерфейс для работы с KML/KMZ."""

from __future__ import annotations

import argparse
import re
from typing import Dict, List, Optional, Sequence

from pathlib import Path

from . import services
from .io_utils import ask_file_path, ask_folder_name, ask_mode, get_user_input
from .logging_config import get_logger


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


def _collect_placemark_names(manager) -> List[str]:
    """Возвращает список имён всех меток для автодополнения."""
    names: List[str] = []
    if manager.root is None:
        return names

    namespace = manager.namespace
    name_xpath = f"{namespace}name" if namespace else "name"

    for pm in manager.placemarks:
        name_elem = pm.find(name_xpath)
        if name_elem is not None and name_elem.text:
            names.append(name_elem.text)

    return names


def _interactive_session_for_file(input_path: Path) -> None:
    """Основной сценарий работы с одним файлом."""
    logger = get_logger(__name__)

    try:
        manager = services.load_document(input_path)
    except RuntimeError as e:
        logger.error("%s", e)
        print(e)
        return

    logger.info("Загружено меток: %d", len(manager.placemarks))
    logger.info("Найдено папок: %d", len(manager.folders))

    placemark_names = _collect_placemark_names(manager)
    setup_readline(placemark_names)

    # Папки и метки, созданные/выбранные в текущей сессии
    session_folders: Dict[str, List[object]] = {}

    while True:
        folder_name = ask_folder_name()
        if folder_name is None:
            # Отмена создания папки
            continue

        folder = services.get_or_create_folder(manager, folder_name)
        if folder is None:
            # Не удалось создать/получить папку
            continue

        mode = ask_mode()
        if mode is None:
            # Отмена выбора режима
            continue

        patterns: List[str] = []

        if mode == "1":
            print("Вводите названия меток по одной (пустая строка для завершения):")
            while True:
                pattern = get_user_input("Метка: ")
                if pattern is None:
                    break
                if not pattern:
                    break
                patterns.append(pattern)
        else:
            batch_prompt = "Введите названия меток через запятую или двоеточие: "
            patterns_input = get_user_input(batch_prompt)

            if patterns_input is None:
                continue

            patterns = parse_patterns(patterns_input)

        if not patterns:
            logger.warning("Не введены метки для поиска")
            continue

        use_regex = len(patterns) == 1 and any(
            c in patterns[0] for c in ["*", "?", ".", "^", "$", "[", "]"]
        )
        if use_regex:
            confirm = get_user_input(
                f"Использовать regex-поиск '{patterns[0]}'? (y/N): ",
                lambda x: x.lower() in ("y", "n", "д", "н"),
            )
            if confirm is None:
                continue
            use_regex = confirm.lower() in ("y", "д")

        found, not_found = services.find_placemarks(manager, patterns, use_regex)

        if not found:
            logger.warning("Метки не найдены")
            continue

        print(f"Найдено меток: {len(found)}")
        namespace = manager.namespace
        name_xpath = f"{namespace}name" if namespace else "name"

        if len(found) > 10:
            print("Первые 10 найденных меток:")
            for i, pm in enumerate(found[:10]):
                name_elem = pm.find(name_xpath)
                name = name_elem.text if name_elem is not None else "Без названия"
                print(f"  {i + 1}. {name}")
            print("... и еще", len(found) - 10, "меток")
        else:
            print("Найденные метки:")
            for i, pm in enumerate(found):
                name_elem = pm.find(name_xpath)
                name = name_elem.text if name_elem is not None else "Без названия"
                print(f"  {i + 1}. {name}")

        confirm_move = get_user_input(
            "Подтвердите перемещение (y/N): ",
            lambda x: x.lower() in ("y", "n", "д", "н", ""),
        )
        if confirm_move is None or confirm_move.lower() in ("n", "н", ""):
            continue

        # Вместо реального перемещения в исходном документе
        # запоминаем выбор пользователя для последующего экспорта.
        folder_entries = session_folders.setdefault(folder_name, [])
        added = 0
        for pm in found:
            if pm not in folder_entries:
                folder_entries.append(pm)
                added += 1

        logger.info("Добавлено меток в папку '%s': %d", folder_name, added)

        if not_found:
            logger.warning("Не найдены: %s", ", ".join(not_found))

        continue_answer = get_user_input(
            "Создать еще одну папку? (y/N): ",
            lambda x: x.lower() in ("y", "n", "д", "н", ""),
        )
        if continue_answer is None or continue_answer.lower() in ("n", "н", ""):
            break

    if not session_folders:
        logger.info("Папки и метки для экспорта не были выбраны.")
        print("Не выбраны папки или метки для экспорта. Новый файл не создан.")
        return

    output_name = f"{input_path.stem}_groups.kmz"
    output_path = input_path.parent / output_name

    exported = services.export_folders_to_kmz(manager, session_folders, output_path)

    if exported:
        logger.info("Экспортированный KMZ файл сохранён: %s", exported)
        print(f"Экспортированный KMZ файл сохранён: {exported}")
    else:
        logger.error("Не удалось создать экспортированный KMZ файл.")
        print("Не удалось создать экспортированный KMZ файл.")


def _show_instructions() -> None:
    """Печатает инструкцию для пользователя."""
    print(
        "\n\tВажно!\n"
        "Для стабильной работы используйте kmz или kml файлы\n"
        "Скопировать полный путь к файлу: Зажать Shift + ПКМ на файл - 'Копировать как путь'\n"
        "Ссылка должна быть вида: C:\\Папка\\Рабочий стол\\Метки.kmz (без кавычек)\n"
        "Для отмены операции введите !cancel, для выхода - !exit\n"
        "\n\tExcel\n"
        "Преобразование вертикального списка в горизонтальный:\n"
        "В пустой ячейке поставьте «=», выделите мышью нужный диапазон со значениями вертикального списка и нажмите F9.\n"
        "В появившейся формуле удалите фигурные скобки, знак «=» и знак «+».\n"
        "Затем через поиск и замену (Ctrl+H) замените двоеточие «:» или запятую «,» на нужный вам разделитель.\n"
    )
    print("Нажмите «ENTER» для продолжения: ")
    input()


def run_menu() -> None:
    """Главное меню: выбор действия пользователем."""
    while True:
        try:
            print(
                "Выберите действие:\n"
                "Запустить программу - нажмите: 1\n"
                "Читать инструкцию - нажмите: 2"
            )
            user_tap_str = get_user_input("Введите свой ответ: ", lambda x: x in ("1", "2"))
            if user_tap_str is None:
                # Пользователь прервал ввод, возвращаемся к началу цикла
                continue

            user_tap = int(user_tap_str)

            if user_tap == 2:
                _show_instructions()
            elif user_tap == 1:
                parser = argparse.ArgumentParser(description="KML/KMZ Placemark Organizer")
                parser.add_argument("file_path", help="Путь к KML/KMZ файлу", nargs="?")
                args = parser.parse_args()

                if args.file_path:
                    input_path = Path(args.file_path)
                else:
                    input_path = ask_file_path()
                    if input_path is None:
                        # Отмена ввода пути
                        continue

                _interactive_session_for_file(input_path)
            else:
                print("Неверное значение! Попробуйте еще раз!")

        except ValueError:
            print("Ошибка! Введите число (1 или 2)")


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Точка входа CLI."""
    # Пока игнорируем argv и используем встроенный парсер argparse внутри меню,
    # чтобы сохранить текущее поведение запуска.
    run_menu()


if __name__ == "__main__":
    main()

