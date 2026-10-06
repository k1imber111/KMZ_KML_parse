"""Самопроверка kml_organizer. Запуск: python test_kml_organizer.py"""

import builtins
import logging
import tempfile
import zipfile
from pathlib import Path

from kml_organizer import services
from kml_organizer.cli import _interactive_session_for_file, parse_patterns
from kml_organizer.core import KmlDocumentManager
from kml_organizer.io_utils import ask_file_path, get_user_input

logging.disable(logging.CRITICAL)

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
<Document>
  <Style id="red"><IconStyle><Icon><href>files/red.png</href></Icon></IconStyle></Style>
  <Placemark><name>10</name><styleUrl>#red</styleUrl><Point><coordinates>1,2</coordinates></Point></Placemark>
  <Placemark><name> 11 </name><Point><coordinates>1,2</coordinates></Point></Placemark>
  <Placemark><name>100</name><gx:Track/></Placemark>
  <Placemark><name>A[1]</name></Placemark>
</Document>
</kml>"""


def make_kmz(folder: Path) -> Path:
    src = folder / "src.kmz"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("doc.kml", KML)
        z.writestr("files/red.png", b"PNG")
    return src


def names(manager: KmlDocumentManager, placemarks) -> list:
    return [manager.name_of(pm) for pm in placemarks]


def feed_input(*lines: str) -> None:
    """Подменяет input() заранее заданными ответами; после них — EOF."""
    answers = iter(lines)

    def fake_input(prompt: str = "") -> str:
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    builtins.input = fake_input


def test_search(tmp: Path) -> None:
    m = services.load_document(make_kmz(tmp))

    found, not_found = m.find_placemarks(["11"])
    assert names(m, found) == ["11"], "имя с пробелами по краям должно находиться"

    found, not_found = m.find_placemarks(["10", "10", "нет"])
    assert names(m, found) == ["10"] and not_found == ["нет"], "дубль шаблона не попадает в «не найдены»"

    found, _ = m.find_placemarks(["10*"])
    assert names(m, found) == ["10", "100"], "маска * — любые символы"

    found, not_found = m.find_placemarks(["*0", "1?"])
    assert names(m, found) == ["10", "100", "11"] and not not_found, "метка не повторяется при пересечении масок"

    found, _ = m.find_placemarks(["A[1]"])
    assert names(m, found) == ["A[1]"], "точное имя важнее маски"

    assert parse_patterns("10:11, 12,,13") == ["10", "11", "12", "13"]


def test_export(tmp: Path) -> None:
    src = make_kmz(tmp)
    m = services.load_document(src)
    found, _ = m.find_placemarks(["10", "100"])

    bystander = tmp / "src_groups.kml"
    bystander.write_text("чужой файл", encoding="utf-8")

    out = services.export_folders_to_kmz(m, {"Папка": found}, tmp / "src_groups.kmz")
    assert out is not None
    assert bystander.read_text(encoding="utf-8") == "чужой файл", "экспорт не трогает соседние файлы"

    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["doc.kml", "files/red.png"], "иконки копируются из исходного KMZ"
        doc = z.read("doc.kml").decode("utf-8")

    assert "ns0" not in doc and "<Placemark>" in doc and "<Folder>" in doc, doc
    assert '<Style id="red">' in doc, "общие стили переносятся в экспорт"
    assert "<gx:Track" in doc, "префикс gx сохраняется"

    again = services.load_document(out)
    assert names(again, again.placemarks) == ["10", "100"], "результат читается обратно"


def test_encoding_and_structure(tmp: Path) -> None:
    bad = tmp / "bad.kml"
    bad.write_bytes(
        '<?xml version="1.0" encoding="UTF-8"?><kml><Document><Placemark>'
        "<name>Стоянка 5</name></Placemark></Document></kml>".encode("cp1251")
    )
    m = KmlDocumentManager(bad)
    assert m.parse_file() and names(m, m.placemarks) == ["Стоянка 5"], "cp1251 под видом UTF-8 не теряет буквы"

    multi = tmp / "multi.kml"
    multi.write_text(
        "<kml><Folder><Document><Placemark><name>a</name></Placemark></Document>"
        "<Document><Placemark><name>b</name></Placemark></Document></Folder></kml>",
        encoding="utf-8",
    )
    m = KmlDocumentManager(multi)
    assert m.parse_file() and names(m, m.placemarks) == ["a", "b"], "метки ищутся во всех Document"

    broken = tmp / "broken.kml"
    broken.write_text("<kml><Document>", encoding="utf-8")
    assert not KmlDocumentManager(broken).parse_file(), "битый XML — отказ, а не исключение"


def test_input(tmp: Path) -> None:
    src = make_kmz(tmp)

    feed_input(f'"{src}"')
    assert ask_file_path() == src, "путь в кавычках принимается"

    feed_input()
    try:
        get_user_input("? ")
    except SystemExit:
        pass
    else:
        raise AssertionError("закрытый ввод должен завершать программу, а не зацикливать её")


def test_session(tmp: Path) -> None:
    src = make_kmz(tmp)

    # Папка А: 10 и 11. Папка Б: снова 10 — метка должна переехать, а не задвоиться.
    # Затем неудачный поиск и ENTER вместо имени папки — файл всё равно сохраняется.
    feed_input(
        "А", "2", "10, 11", "y", "y",
        "Б", "1", "10", "", "y", "y",
        "В", "2", "нет такой",
        "",
    )
    _interactive_session_for_file(src)

    out = services.load_document(tmp / "src_groups.kmz")
    ns = out.namespace
    folders = {
        folder.findtext(f"{ns}name"): names(out, folder.iter(f"{ns}Placemark"))
        for folder in out.root.iter(f"{ns}Folder")
    }
    assert folders == {"А": ["11"], "Б": ["10"]}, folders

    # !cancel в интерактивном режиме отменяет папку: меток нет — файла нет
    (tmp / "src_groups.kmz").unlink()
    feed_input("А", "1", "10", "!cancel", "")
    _interactive_session_for_file(src)
    assert not (tmp / "src_groups.kmz").exists()


if __name__ == "__main__":
    real_input = builtins.input
    tests = [test_search, test_export, test_encoding_and_structure, test_input, test_session]
    try:
        for test in tests:
            with tempfile.TemporaryDirectory() as tmp_dir:
                test(Path(tmp_dir))
            print("ok  ", test.__name__)
    finally:
        builtins.input = real_input
    print("Все проверки пройдены.")
