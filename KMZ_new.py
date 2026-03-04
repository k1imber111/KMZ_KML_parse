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

import logging

from kml_organizer.cli import main


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Произошла непредвиденная ошибка: {e}")
        logging.error("Произошла непредвиденная ошибка: %s", e, exc_info=True)