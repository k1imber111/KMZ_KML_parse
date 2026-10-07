"""Самопроверка kml_organizer. Запуск: python test_kml_organizer.py"""

import builtins
import contextlib
import io
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from kml_organizer import services
from kml_organizer.cli import _free_output_path, _interactive_session_for_file, parse_patterns, run_menu
from kml_organizer.core import KmlDocumentManager
from kml_organizer.io_utils import ask_file_path, get_user_input, normalize_path, resolve_input_path

logging.disable(logging.CRITICAL)

SAMPLES = Path(__file__).parent / "Образцы файлов"
DECL = '<?xml version="1.0" encoding="UTF-8"?>'
NS22 = "http://www.opengis.net/kml/2.2"


def kml(placemarks: str, ns: str = NS22, extra: str = "") -> str:
    """Минимальный KML: kml -> Document -> метки."""
    xmlns = f' xmlns="{ns}"' if ns else ""
    return f"<kml{xmlns}{extra}><Document>{placemarks}</Document></kml>"


def pm(name: str, inner: str = "") -> str:
    return f"<Placemark><name>{name}</name>{inner}</Placemark>"


# Повторяет структуру файла Google Earth Pro: kml -> Folder -> метки со встроенным стилем
GE_NAMES = ["1_Machta_sotovoi", "10_Retransliator", "100_Zdanie", "10А", "11", "11_Truba", "Стоянка  5", "253_BAU_в„–17"]
GE_KML = (
    DECL
    + f'\n<kml xmlns="{NS22}" xmlns:gx="http://www.google.com/kml/ext/2.2" xmlns:kml="{NS22}"'
    ' xmlns:atom="http://www.w3.org/2005/Atom">\n<Folder>\n\t<name>2022</name>\n\t<visibility>0</visibility>\n'
    "\t<Style><ListStyle><listItemType>check</listItemType></ListStyle></Style>\n"
    + "".join(
        f"\t<Placemark>\n\t\t<name>{name}</name>\n\t\t<visibility>0</visibility>\n"
        "\t\t<description><![CDATA[973.73m <br>styleUrl: root://styleMaps#default]]></description>\n"
        "\t\t<Style><IconStyle><Icon><href>files/ltblu-blank.png</href></Icon></IconStyle></Style>\n"
        "\t\t<Point><extrude>1</extrude><coordinates>40.28,43.70,0</coordinates></Point>\n\t</Placemark>\n"
        for name in GE_NAMES
    )
    + '\t<atom:link rel="app" href="https://www.google.com/earth/" title="Google Earth Pro"></atom:link>\n'
    "</Folder>\n</kml>\n"
)


def make_ge_kmz(folder: Path, name: str = "src.kmz") -> Path:
    src = folder / name
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("doc.kml", GE_KML)
        z.writestr("files/ltblu-blank.png", b"PNG")
    return src


def names(manager: KmlDocumentManager, placemarks) -> list:
    return [manager.name_of(p) for p in placemarks]


def feed_input(*lines: str) -> None:
    """Подменяет input() заранее заданными ответами; после них — EOF."""
    answers = iter(lines)

    def fake_input(prompt: str = "") -> str:
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    builtins.input = fake_input


def folders_of(path: Path) -> dict:
    """Читает результат: папка -> имена меток."""
    out = services.load_document(path)
    ns = out.namespace
    return {
        folder.findtext(f"{ns}name"): names(out, folder.iter(f"{ns}Placemark"))
        for folder in out.root.iter(f"{ns}Folder")
    }


# ---------- Пути ----------


def test_normalize_path(tmp: Path) -> None:
    expected = Path("C:/Карты/Метки.kmz")
    for raw in (
        '"C:/Карты/Метки.kmz"',
        "'C:/Карты/Метки.kmz'",
        "«C:/Карты/Метки.kmz»",
        "“C:/Карты/Метки.kmz”",
        '   "C:/Карты/Метки.kmz"  ',
        'C:/Карты/Метки.kmz"',
        '" C:/Карты/Метки.kmz "',
    ):
        assert normalize_path(raw) == expected, raw

    os.environ["KML_TEST_DIR"] = str(tmp)
    assert normalize_path('"$KML_TEST_DIR/a.kml"') == tmp / "a.kml", "переменные окружения раскрываются"


def test_resolve_path(tmp: Path) -> None:
    empty = tmp / "пустая"
    empty.mkdir()
    single = tmp / "одна"
    single.mkdir()
    only = make_ge_kmz(single, "Метки.KMZ")
    many = tmp / "папка с пробелом"
    many.mkdir()
    b = make_ge_kmz(many, "Б.kmz")
    a = many / "а.kml"
    a.write_text(GE_KML, encoding="utf-8")
    (many / "заметки.txt").write_text("не карта", encoding="utf-8")
    (many / "вложенная").mkdir()
    make_ge_kmz(many / "вложенная", "глубоко.kmz")

    assert resolve_input_path(f'"{a}"') == a, "файл в кавычках"
    assert resolve_input_path(str(many / "заметки.txt")) is None, "чужое расширение отклоняется"
    assert resolve_input_path(str(tmp / "нет такого.kmz")) is None, "несуществующий путь"
    assert resolve_input_path(f"'{empty}'") is None, "папка без карт"
    assert resolve_input_path(f'"{single}"') == only, "единственный файл берётся сам, регистр расширения не важен"

    feed_input("2")
    assert resolve_input_path(f'"{many}"') == b, "выбор по номеру, подпапки и чужие файлы не в списке"
    feed_input("0", "3", "abc", "1")
    assert resolve_input_path(str(many)) == a, "неверный номер переспрашивается"
    feed_input("!cancel")
    assert resolve_input_path(str(many)) is None, "!cancel отменяет выбор"

    feed_input("нет такого", f'"{many}"', "!cancel", f'"{many}\\"', "2")
    assert ask_file_path() == b, "после ошибок и отмены путь спрашивается заново"


# ---------- Варианты файлов ----------


def test_file_variants(tmp: Path) -> None:
    body = kml(pm("Стоянка 5"))
    old_ns = "http://earth.google.com/kml/2.1"
    variants = {
        "обычный UTF-8": (DECL + body).encode("utf-8"),
        "пустая строка перед <?xml": ("\n\n" + DECL + body).encode("utf-8"),
        "UTF-8 с BOM": b"\xef\xbb\xbf" + (DECL + body).encode("utf-8"),
        "BOM и перенос перед <?xml": b"\xef\xbb\xbf\r\n" + (DECL + body).encode("utf-8"),
        "UTF-16": ('<?xml version="1.0" encoding="UTF-16"?>' + body).encode("utf-16"),
        "cp1251 с объявлением": ('<?xml version="1.0" encoding="windows-1251"?>' + body).encode("cp1251"),
        "cp1251 без объявления": body.encode("cp1251"),
        "cp1251 под видом UTF-8": (DECL + body).encode("cp1251"),
        "старый namespace": (DECL + kml(pm("Стоянка 5"), ns=old_ns)).encode("utf-8"),
        "без namespace": (DECL + kml(pm("Стоянка 5"), ns="")).encode("utf-8"),
        "корень Document": (DECL + f"<Document>{pm('Стоянка 5')}</Document>").encode("utf-8"),
        "CDATA в имени": (DECL + kml(pm("<![CDATA[ Стоянка 5 ]]>"))).encode("utf-8"),
        "gx без объявления": (DECL + kml(pm("Стоянка 5", "<gx:Track/>"))).encode("utf-8"),
        "голый & в описании": (DECL + kml(pm("Стоянка 5", "<description>A & B&nbsp;C</description>"))).encode("utf-8"),
    }

    for label, data in variants.items():
        src = tmp / f"{len(label)}_{abs(hash(label))}.kml"
        src.write_bytes(data)
        m = services.load_document(src)
        assert names(m, m.placemarks) == ["Стоянка 5"], label

        found, not_found = m.find_placemarks(["Стоянка 5"])
        out = services.export_folders_to_kmz(m, {"Папка": found}, src.with_suffix(".out.kmz"))
        assert out is not None and not not_found, label
        assert folders_of(out) == {"Папка": ["Стоянка 5"]}, f"{label}: результат читается обратно"
        with zipfile.ZipFile(out) as z:
            assert b"ns0" not in z.read("doc.kml"), f"{label}: без префикса ns0"

    # Починка разметки не трогает содержимое CDATA
    src = tmp / "cdata.kml"
    html = "<b>A & B</b>&nbsp;<br>"
    src.write_text(DECL + kml(pm("R & D", f"<description><![CDATA[{html}]]></description>")), encoding="utf-8")
    m = services.load_document(src)
    assert names(m, m.placemarks) == ["R & D"]
    assert m.placemarks[0].findtext(f"{m.namespace}description") == html, "CDATA сохранён дословно"

    broken = tmp / "broken.kml"
    broken.write_text("<kml><Document>", encoding="utf-8")
    assert not KmlDocumentManager(broken).parse_file(), "битый XML — отказ, а не исключение"


def test_kmz_variants(tmp: Path) -> None:
    # Главный KML — не первый в архиве и назван не doc.kml
    src = tmp / "nested.kmz"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("files/extra.kml", kml(pm("лишняя")))
        z.writestr("Мои метки.kml", kml(pm("нужная")))
    m = services.load_document(src)
    assert names(m, m.placemarks) == ["нужная"], "берётся KML из корня архива"

    src = tmp / "two.kmz"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("a.kml", kml(pm("лишняя")))
        z.writestr("doc.kml", kml(pm("нужная")))
    m = services.load_document(src)
    assert names(m, m.placemarks) == ["нужная"], "doc.kml важнее других KML"

    # Расширение не соответствует содержимому
    as_kml = tmp / "archive.kml"
    shutil.copy(make_ge_kmz(tmp), as_kml)
    m = services.load_document(as_kml)
    assert len(m.placemarks) == len(GE_NAMES), "KMZ с расширением .kml"
    out = services.export_folders_to_kmz(m, {"П": m.placemarks[:1]}, tmp / "archive_out.kmz")
    with zipfile.ZipFile(out) as z:
        assert "files/ltblu-blank.png" in z.namelist(), "вложения переносятся и при неверном расширении"

    as_kmz = tmp / "plain.kmz"
    as_kmz.write_text(GE_KML, encoding="utf-8")
    m = services.load_document(as_kmz)
    assert len(m.placemarks) == len(GE_NAMES), "KML с расширением .kmz"

    for name, content, expected in (
        ("link.kml", kml("<NetworkLink><Link><href>http://x/y.kml</href></Link></NetworkLink>"), "NetworkLink"),
        ("empty.kml", kml(""), "не найдено меток"),
        ("garbage.kml", "это не карта", "Не удалось разобрать"),
    ):
        path = tmp / name
        path.write_text(content, encoding="utf-8")
        try:
            services.load_document(path)
        except RuntimeError as e:
            assert expected in str(e), (name, str(e))
        else:
            raise AssertionError(f"{name}: ожидался отказ")


# ---------- Поиск ----------


def test_search(tmp: Path) -> None:
    m = services.load_document(make_ge_kmz(tmp))

    def find(*queries: str) -> tuple:
        found, not_found = m.find_placemarks(list(queries))
        return names(m, found), not_found

    assert find("10") == (["10_Retransliator"], []), "номер в начале имени; 100_ и 10А не подходят"
    assert find("1") == (["1_Machta_sotovoi"], [])
    assert find("11") == (["11"], []), "точное имя важнее номера"
    assert find("253") == (["253_BAU_в„–17"], []), "испорченный знак в имени не мешает поиску по номеру"
    assert find("Стоянка 5", " Стоянка   5 ") == (["Стоянка 5"], []), "лишние пробелы не важны"
    assert find("10", "10", "999") == (["10_Retransliator"], ["999"]), "дубль запроса не попадает в «не найдены»"
    assert find("10*") == (["10_Retransliator", "100_Zdanie", "10А"], []), "маска"
    assert find("1?_*", "10") == (["10_Retransliator", "11_Truba"], []), "метка не повторяется"
    assert find("10_Retransliator", "retransliator") == (["10_Retransliator"], ["retransliator"]), "регистр учитывается"

    assert parse_patterns("10:11, 12,,13") == ["10", "11", "12", "13"]
    assert parse_patterns("10;11; 12") == ["10", "11", "12"], "точка с запятой — разделитель"
    assert parse_patterns('={"10А":"11Б"}') == ["10А", "11Б"], "строка из Excel чистится сама"
    assert parse_patterns("={10:11:12}") == ["10", "11", "12"]


# ---------- Экспорт ----------


def test_export(tmp: Path) -> None:
    src = make_ge_kmz(tmp)
    m = services.load_document(src)
    found, _ = m.find_placemarks(["10", "100"])

    bystander = tmp / "src_groups.kml"
    bystander.write_text("чужой файл", encoding="utf-8")

    out = services.export_folders_to_kmz(m, {"Папка": found}, tmp / "src_groups.kmz")
    assert out is not None
    assert bystander.read_text(encoding="utf-8") == "чужой файл", "экспорт не трогает соседние файлы"

    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["doc.kml", "files/ltblu-blank.png"], "иконки копируются из исходного KMZ"
        doc = z.read("doc.kml").decode("utf-8")
    assert "ns0" not in doc and "<Placemark>" in doc and "<Folder>" in doc, doc
    assert doc.count("<href>files/ltblu-blank.png</href>") == 2, "встроенные стили меток сохраняются"
    assert folders_of(out) == {"Папка": ["10_Retransliator", "100_Zdanie"]}

    # Общие стили: в Document и в Folder; стиль папки без id общим не считается
    shared = tmp / "shared.kml"
    shared.write_text(
        DECL
        + kml(
            '<Style id="doc"><IconStyle/></Style>'
            '<Folder><Style><ListStyle/></Style><Style id="fld"><IconStyle/></Style>'
            + pm("a", "<styleUrl>#fld</styleUrl>")
            + "</Folder>",
            extra=' xmlns:gx="http://www.google.com/kml/ext/2.2"',
        ),
        encoding="utf-8",
    )
    m = services.load_document(shared)
    out = services.export_folders_to_kmz(m, {"П": m.placemarks}, tmp / "shared_out.kmz")
    with zipfile.ZipFile(out) as z:
        doc = z.read("doc.kml").decode("utf-8")
    assert '<Style id="doc">' in doc and '<Style id="fld">' in doc and "ListStyle" not in doc, doc

    # Иконки рядом с .kml попадают в архив; ссылки наружу и в интернет — нет
    icons = tmp / "icons"
    (icons / "sub").mkdir(parents=True)
    (icons / "a.png").write_bytes(b"A")
    (icons / "sub" / "b.png").write_bytes(b"B")
    (tmp / "outside.png").write_bytes(b"X")
    hrefs = ["a.png", "sub/b.png", "missing.png", "../outside.png", "http://example.com/c.png"]
    local = icons / "local.kml"
    local.write_text(
        DECL + kml("".join(pm(str(i), f"<Style><IconStyle><Icon><href>{h}</href></Icon></IconStyle></Style>") for i, h in enumerate(hrefs))),
        encoding="utf-8",
    )
    m = services.load_document(local)
    out = services.export_folders_to_kmz(m, {"П": m.placemarks}, icons / "local_out.kmz")
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["a.png", "doc.kml", "sub/b.png"], z.namelist()

    # Имя результата не занимает существующие файлы
    target = tmp / "карта.kml"
    assert _free_output_path(target).name == "карта_groups.kmz"
    (tmp / "карта_groups.kmz").touch()
    assert _free_output_path(target).name == "карта_groups_2.kmz"
    (tmp / "карта_groups_2.kmz").touch()
    assert _free_output_path(target).name == "карта_groups_3.kmz"


# ---------- Ввод и сессия ----------


def test_input(tmp: Path) -> None:
    feed_input()
    try:
        get_user_input("? ")
    except SystemExit:
        pass
    else:
        raise AssertionError("закрытый ввод должен завершать программу, а не зацикливать её")


def test_session(tmp: Path) -> None:
    src = make_ge_kmz(tmp)

    # Папка А: 10 и 11. Папка Б: снова 10 — метка должна переехать, а не задвоиться.
    # Неудачный поиск, случайный ENTER с отказом от завершения, ещё одна папка, ENTER и подтверждение.
    feed_input(
        "А", "2", "10; 11", "y", "y",
        "Б", "1", "10", "", "y", "y",
        "В", "2", "нет такой",
        "", "n",
        "В", "2", "{\"100\"}", "y", "y",
        "", "",
    )
    _interactive_session_for_file(src)
    assert folders_of(tmp / "src_groups.kmz") == {"А": ["11"], "Б": ["10_Retransliator"], "В": ["100_Zdanie"]}

    # Повторный запуск не перезаписывает прошлый результат
    feed_input("Г", "2", "1", "y", "n")
    _interactive_session_for_file(src)
    assert folders_of(tmp / "src_groups_2.kmz") == {"Г": ["1_Machta_sotovoi"]}
    assert folders_of(tmp / "src_groups.kmz").keys() == {"А", "Б", "В"}, "первый результат цел"

    # !cancel в интерактивном режиме отменяет папку: меток нет — файла нет
    feed_input("А", "1", "10", "!cancel", "")
    _interactive_session_for_file(src)
    assert not (tmp / "src_groups_3.kmz").exists()


def test_menu(tmp: Path) -> None:
    folder = tmp / "Мои карты"
    folder.mkdir()
    make_ge_kmz(folder, "2022.kmz")
    (folder / "2022.kml").write_text(GE_KML.replace("files/ltblu-blank.png", "ltblu-blank.png"), encoding="utf-8")

    def run(*answers: str, initial: str = None) -> None:
        feed_input(*answers)
        try:
            run_menu(initial)
        except SystemExit:
            pass

    # Путь к папке в кавычках -> выбор файла -> папка -> сохранение
    run("1", f'"{folder}"', "2", "Мачты", "2", "1, 10", "y", "n")
    assert folders_of(folder / "2022_groups.kmz") == {"Мачты": ["1_Machta_sotovoi", "10_Retransliator"]}
    with zipfile.ZipFile(folder / "2022_groups.kmz") as z:
        assert "files/ltblu-blank.png" in z.namelist()

    # Путь из аргумента командной строки; у .kml и .kmz результат не затирает друг друга
    run("1", "1", "Здания", "2", "100", "y", "n", initial=f"'{folder}'")
    assert folders_of(folder / "2022_groups_2.kmz") == {"Здания": ["100_Zdanie"]}

    # Неверный аргумент не зацикливает: программа спрашивает путь сама
    run("1", str(folder / "2022.kmz"), "Трубы", "2", "11_Truba", "y", "n", initial=str(tmp / "нет.kmz"))
    assert folders_of(folder / "2022_groups_3.kmz") == {"Трубы": ["11_Truba"]}


def test_real_samples(tmp: Path) -> None:
    files = [p for p in SAMPLES.glob("*") if p.suffix.lower() in (".kml", ".kmz")] if SAMPLES.is_dir() else []
    if not files:
        raise LookupError("папка «Образцы файлов» отсутствует")

    for original in files:
        src = Path(shutil.copy(original, tmp))
        m = services.load_document(src)
        assert m.placemarks, original.name

        first = names(m, m.placemarks[:3])
        queries = [name.split("_")[0] for name in first]
        found, not_found = m.find_placemarks(queries)
        assert names(m, found) == first and not not_found, (original.name, queries, names(m, found))

        out = services.export_folders_to_kmz(m, {"Проверка": found}, tmp / f"{src.name}.out.kmz")
        assert folders_of(out) == {"Проверка": first}, original.name
        with zipfile.ZipFile(out) as z:
            assert b"ns0" not in z.read("doc.kml"), original.name
            if m.kml_entry is not None:
                with zipfile.ZipFile(src) as source:
                    attachments = {n for n in source.namelist() if n != m.kml_entry and not n.endswith("/")}
                assert attachments <= set(z.namelist()), f"{original.name}: вложения перенесены"

        # Полный выбор: каждая метка находится по своему имени, и результат содержит их все
        everything, not_found = m.find_placemarks(names(m, m.placemarks))
        assert len(everything) == len(m.placemarks) and not not_found, original.name
        out = services.export_folders_to_kmz(m, {"Все": everything}, tmp / f"{src.name}.all.kmz")
        assert len(services.load_document(out).placemarks) == len(m.placemarks), original.name


if __name__ == "__main__":
    real_input = builtins.input
    tests = [
        test_normalize_path, test_resolve_path, test_file_variants, test_kmz_variants,
        test_search, test_export, test_input, test_session, test_menu, test_real_samples,
    ]
    try:
        for test in tests:
            screen = io.StringIO()
            try:
                with tempfile.TemporaryDirectory() as tmp_dir, contextlib.redirect_stdout(screen):
                    test(Path(tmp_dir))
            except LookupError as e:
                print("skip", test.__name__, f"({e})")
            except Exception:
                print("FAIL", test.__name__)
                print(screen.getvalue()[-1500:])
                raise
            else:
                print("ok  ", test.__name__)
    finally:
        builtins.input = real_input
    print("Все проверки пройдены.")
