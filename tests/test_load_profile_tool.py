"""Tests for the browser load profile generator (docs/assets/load-profile-generator.html).

The calculation core is JavaScript. These tests build real XLSX files (ZIP + XML,
written by hand so no Excel library is needed), run them through the core with
node and compare the result with a pandas reference.
"""
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "js" / "run_xlsx.js"
JS_TESTS = ROOT / "tests" / "js" / "load_profile_core.test.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed")


def _col(idx):
    return chr(ord("A") + idx)


def write_xlsx(path, header, units, rows, *, shared=True, serial_dates=False):
    """Write a minimal xlsx: row 1 names, row 2 units, then data rows.

    rows: list of (datetime, [values]); NaN values are written as empty cells.
    """
    strings = {}

    def sst(text):
        return strings.setdefault(text, len(strings))

    def text_cell(ref, text):
        if shared:
            return f'<c r="{ref}" t="s"><v>{sst(text)}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'

    sheet = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData>']
    sheet.append('<row r="1">' + "".join(
        text_cell(f"{_col(i)}1", h) for i, h in enumerate(header)) + "</row>")
    sheet.append('<row r="2">' + "".join(
        text_cell(f"{_col(i)}2", u) for i, u in enumerate(units)) + "</row>")
    for n, (ts, values) in enumerate(rows, start=3):
        if serial_dates:
            serial = (ts - pd.Timestamp("1899-12-30")).total_seconds() / 86400
            cells = [f'<c r="A{n}"><v>{float(serial)!r}</v></c>']
        else:
            cells = [text_cell(f"A{n}", ts.strftime("%d.%m.%Y %H:%M"))]
        for i, val in enumerate(values, start=1):
            if not np.isnan(val):
                cells.append(f'<c r="{_col(i)}{n}"><v>{float(val)!r}</v></c>')
        sheet.append(f'<row r="{n}">' + "".join(cells) + "</row>")
    sheet.append("</sheetData></worksheet>")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"/>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/'
                   'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
                   'officeDocument/2006/relationships"><sheets>'
                   '<sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.'
                   'org/package/2006/relationships"><Relationship Id="rId1" Type="worksheet" '
                   'Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", "".join(sheet))
        if shared:
            z.writestr("xl/sharedStrings.xml",
                       '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/'
                       'spreadsheetml/2006/main">' + "".join(
                           f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>")


def run_core(path, **opts):
    proc = subprocess.run(["node", str(RUNNER), str(path), json.dumps(opts)],
                          capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def synthetic_year(seed=1, wallbox=False):
    """One year of 5 minute data with a known weekly shape plus noise."""
    idx = pd.date_range("2023-01-02 00:00", "2024-01-01 23:55", freq="5min")
    rng = np.random.default_rng(seed)
    base = (60 + 25 * np.sin(idx.hour / 24 * 2 * np.pi)
            + 10 * (idx.day_of_week >= 5) + 4 * np.cos(idx.month / 12 * 2 * np.pi))
    base = base + rng.normal(0, 3, len(idx))
    wb = np.zeros(len(idx))
    if wallbox:
        charging = (idx.day_of_week == 1) & (idx.hour >= 18) & (idx.hour < 20)
        wb[charging] = 583.0
    return idx, base, wb


def reference_profile(idx, base):
    df = pd.DataFrame({"v": base}, index=idx)
    # hours without data are gaps (NaN), not 0 Wh - the bug of the PR #124 script
    hourly = df["v"].resample("60min").sum(min_count=1)
    g = hourly.groupby([hourly.index.month, hourly.index.day_of_week, hourly.index.hour]).mean()
    return g.reindex(pd.MultiIndex.from_product(
        [range(1, 13), range(7), range(24)])).to_numpy()


def test_node_unit_tests_pass():
    proc = subprocess.run(["node", "--test", str(JS_TESTS)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("shared,serial_dates", [(True, False), (False, False), (True, True)])
def test_profile_matches_pandas_reference(tmp_path, shared, serial_dates):
    idx, base, _ = synthetic_year()
    path = tmp_path / "export.xlsx"
    rows = [(ts, [v]) for ts, v in zip(idx, base)]
    write_xlsx(path, ["", "Verbrauch"], ["", "[Wh]"], rows,
               shared=shared, serial_dates=serial_dates)
    out = run_core(path)
    assert "error" not in out
    expected = reference_profile(idx, base)
    # the file starts on 2 Jan, so Jan/Mon .. cells exist for all combinations
    np.testing.assert_allclose(out["profile"], expected, rtol=1e-6)
    stats = out["stats"]
    assert stats["gapShare"] == pytest.approx(0, abs=1e-9)
    assert stats["annualKwh"] == pytest.approx(expected.sum() * 8760 / 2016 / 1000, rel=1e-9)
    assert out["csv"].splitlines()[0] == "month,weekday,hour,energy"
    assert len(out["csv"].splitlines()) == 2017


def test_wallbox_removed_and_switchable(tmp_path):
    idx, base, wb = synthetic_year(wallbox=True)
    path = tmp_path / "export.xlsx"
    rows = [(ts, [b + w, w]) for ts, b, w in zip(idx, base, wb)]
    write_xlsx(path, ["", "Verbrauch", "Energie Wattpilot"],
               ["", "[Wh]", "[Wh]"], rows)
    removed = run_core(path, removeWallbox=True)
    kept = run_core(path, removeWallbox=False)
    np.testing.assert_allclose(removed["profile"], reference_profile(idx, base), rtol=1e-6)
    np.testing.assert_allclose(kept["profile"], reference_profile(idx, base + wb), rtol=1e-6)
    assert removed["stats"]["annualKwh"] < kept["stats"]["annualKwh"]
    assert removed["stats"]["wallboxRemoved"] is True
    assert kept["stats"]["wallboxRemoved"] is False
    assert removed["stats"]["wallboxKwh"] == pytest.approx(wb.sum() / 1000, rel=1e-6)
    assert removed["detected"]["wattpilot"] == [1]


def test_gaps_are_filled_not_counted_as_zero(tmp_path):
    idx, base, _ = synthetic_year()
    keep = ~((idx >= "2023-03-06") & (idx < "2023-03-13"))  # one missing week
    path = tmp_path / "export.xlsx"
    rows = [(ts, [v]) for ts, v, k in zip(idx, base, keep) if k]
    write_xlsx(path, ["", "Verbrauch"], ["", "[Wh]"], rows)
    out = run_core(path)
    expected = reference_profile(idx[keep], base[keep])
    np.testing.assert_allclose(out["profile"], expected, rtol=1e-6)
    stats = out["stats"]
    assert stats["gapHours"] == 7 * 24
    assert stats["gapShare"] == pytest.approx(7 * 24 / (365 * 24), rel=0.02)
    assert any(c["level"] == "warn" or c["level"] == "ok" for c in stats["checks"])


def test_partial_year_warns_and_fills_missing_months(tmp_path):
    idx, base, _ = synthetic_year()
    keep = idx < "2023-05-01"
    path = tmp_path / "export.xlsx"
    write_xlsx(path, ["", "Verbrauch"], ["", "[Wh]"],
               [(ts, [v]) for ts, v, k in zip(idx, base, keep) if k])
    stats = run_core(path)["stats"]
    assert stats["missingMonths"] == list(range(5, 13))
    assert not any(np.isnan(v) for v in run_core(path)["profile"])
    levels = [c["level"] for c in stats["checks"]]
    assert "bad" in levels or "warn" in levels
    assert stats["suggestedAnnualKwh"] >= stats["annualKwh"]
    assert stats["suggestedAnnualKwh"] % 100 == 0


def test_missing_consumption_column_is_reported(tmp_path):
    idx, base, _ = synthetic_year()
    path = tmp_path / "export.xlsx"
    write_xlsx(path, ["", "Erzeugung"], ["", "[Wh]"],
               [(ts, [v]) for ts, v in list(zip(idx, base))[:300]])
    out = run_core(path)
    assert out["detected"]["consumption"] == -1
    assert out["columns"][0]["name"] == "Erzeugung"


def test_not_a_xlsx_file_gives_clear_error(tmp_path):
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"this is not a zip file" * 10)
    assert "error" in run_core(path)
