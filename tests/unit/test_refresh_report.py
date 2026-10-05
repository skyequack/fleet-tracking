"""The Word-report refresh helpers, on tiny synthetic XML (the real report is not in the repository)."""
import re

import pytest

from scripts.refresh_report import Report, confusions, ptext, unique_ids

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def para(text, rpr="<w:b/>", ppr="<w:jc/>"):
    return f'<w:p w14:paraId="A{abs(hash(text)) % 9999}"><w:pPr>{ppr}</w:pPr><w:r><w:rPr>{rpr}</w:rPr><w:t>{text}</w:t></w:r></w:p>'


def cell(text):
    return f"<w:tc><w:tcPr><w:tcW/></w:tcPr>{para(text, rpr='')}</w:tc>"


def table(rows):
    return "<w:tbl>" + "".join("<w:tr>" + "".join(cell(c) for c in r) + "</w:tr>" for r in rows) + "</w:tbl>"


def test_set_par_keeps_formatting_and_escapes_text():
    r = Report(f"<w:body {W}>{para('Old text here')}{para('Other')}</w:body>")
    r.set_par("Old text", "New <text> & more")
    assert ptext(re.search(r"<w:p .*?</w:p>", r.xml).group(0)) == "New <text> & more"
    assert "<w:rPr><w:b/></w:rPr>" in r.xml and "<w:jc/>" in r.xml and "Other" in r.xml


def test_a_missing_or_ambiguous_target_stops_the_refresh():
    r = Report(f"<w:body {W}>{para('Same one')}{para('Same two')}</w:body>")
    with pytest.raises(SystemExit):
        r.set_par("Nothing like this", "x")
    with pytest.raises(SystemExit):
        r.set_par("Same", "x")                                    # two paragraphs start with it


def test_mixed_formatting_is_refused_rather_than_flattened():
    mixed = '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Lead. </w:t></w:r><w:r><w:t>Rest</w:t></w:r></w:p>'
    with pytest.raises(SystemExit):
        Report(f"<w:body {W}>{mixed}</w:body>").set_par("Lead.", "x")


def test_table_rows_are_replaced_by_position_even_when_cells_are_identical():
    r = Report(f"<w:body {W}>{table([['K', 'V', 'W'], ['a', '0', '0'], ['b', '0', '0']])}</w:body>")
    r.set_table(["K", "V", "W"], [["a", "0", "7"], ["b", "5", "0"], ["c", "0", "0"]])     # one row more than before
    rows = [[ptext(c) for c in re.findall(r"<w:tc>.*?</w:tc>", row, flags=re.S)] for row in re.findall(r"<w:tr>.*?</w:tr>", r.xml, flags=re.S)]
    assert rows == [["K", "V", "W"], ["a", "0", "7"], ["b", "5", "0"], ["c", "0", "0"]]


def test_table_header_can_be_renamed_and_wrong_width_is_refused():
    r = Report(f"<w:body {W}>{table([['Old', 'Head'], ['x', 'y']])}</w:body>")
    r.set_table(["Old", "Head"], [["p", "q"]], new_header=["New", "Head"])
    assert ptext(re.findall(r"<w:tr>.*?</w:tr>", r.xml, flags=re.S)[0]) == "NewHead"
    with pytest.raises(SystemExit):
        r.set_table(["New", "Head"], [["only one"]])


def test_set_cell_in_row_touches_one_cell():
    r = Report(f"<w:body {W}>{table([['T', 'K', 'F'], ['users', 'id', 'a'], ['trips', 'id', 'a']])}</w:body>")
    r.set_cell_in_row(["T", "K", "F"], "trips", 2, "a, b")
    assert "a, b" in r.xml and r.xml.count("a, b") == 1 and ptext(re.findall(r"<w:tr>.*?</w:tr>", r.xml, flags=re.S)[1]) == "usersida"


def test_replace_text_requires_the_expected_number_of_matches():
    r = Report(f"<w:body {W}>{para('Heading')}{para('Heading')}</w:body>")
    r.replace_text("Heading", "Renamed", 2)
    assert r.xml.count("Renamed") == 2
    with pytest.raises(SystemExit):
        r.replace_text("Renamed", "x", 1)


def test_set_block_can_grow_and_clones_the_last_line():
    r = Report(f"<w:body {W}>{para('code one')}{para('code two')}{para('after')}</w:body>")
    r.set_block("code one", 2, ["a", "b", "c"])
    assert [ptext(p) for p in re.findall(r"<w:p .*?</w:p>", r.xml)] == ["a", "b", "c", "after"]


def test_duplicate_paragraph_ids_are_dropped_but_the_first_is_kept():
    xml = '<w:p w14:paraId="1A"/><w:p w14:paraId="1A"/><w:p w14:paraId="2B"/>'
    assert unique_ids(xml).count("w14:paraId") == 2 and unique_ids(xml).startswith('<w:p w14:paraId="1A"/><w:p/>')


def test_confusion_wording():
    assert confusions(0, "HIGH", "LOW", first=False) == "no HIGH vehicle is classed as LOW"
    assert confusions(1, "LOW", "HIGH") == "One LOW vehicle is classed as HIGH"
    assert confusions(3, "HIGH", "LOW", first=False) == "3 HIGH vehicles are classed as LOW"
