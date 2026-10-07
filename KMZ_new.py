#!/usr/bin/env python3
"""
KML/KMZ
Скрипт для одиночной или пакетной группировки меток в файлах KML/KMZ по заданным папкам.
    version='2.0.0',
    url='https://t.me/Sochi10',
    author='Иван Журавлев',
    author_email='k1imber@yandex.ru'

Основная логика вынесена в пакет kml_organizer.
Этот файл оставлен в качестве тонкой оболочки для запуска из консоли.
"""

from kml_organizer.cli import main
from kml_organizer.logging_config import get_logger


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        get_logger().error("Произошла непредвиденная ошибка: %s", e, exc_info=True)
        # Окно exe закрывается сразу после выхода — даём прочитать сообщение
        try:
            input("Подробности записаны в kml_manager.log. Нажмите ENTER для выхода...")
        except (EOFError, KeyboardInterrupt):
            pass
