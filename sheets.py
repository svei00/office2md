"""Token-lean spreadsheet converter (xlsx / xlsm / xls / csv) for markitdown.

markitdown's stock Excel path goes pandas -> HTML -> Markdown, which pads every
empty cell with "NaN", invents "Unnamed: N" headers, prints dates as
"2024-01-05 00:00:00", and drops formulas that have no cached value. For an LLM
reader all of that is wasted tokens. This converter reads the cells directly
and emits only what is there, in one of these layouts:

- table: compact Markdown table, empty rows/columns removed
- cells: one line per row listing only non-empty cells, e.g. "A7: Item | D7: Total"
         (best for forms, invoices and other sparse sheets)
- csv:   plain CSV per sheet (densest for big data tables)
- auto:  per sheet, title/form rows as cells and the dense block below them
         as a table; sheets with no dense block stay cells
"""

import csv
import datetime as dt
import io
from typing import Any, BinaryIO, Iterable

from markitdown import DocumentConverter, DocumentConverterResult, StreamInfo
from openpyxl.utils import get_column_letter

SHEET_EXTENSIONS = [".xlsx", ".xlsm", ".xls", ".csv"]
MODES = ["auto", "table", "cells", "csv"]

# auto mode: below this share of filled cells (in the trimmed range) a sheet is
# "sparse" and the cell list is cheaper than a table full of empty columns
SPARSE_DENSITY = 0.5

Row = tuple[int, list[str]]  # (1-based sheet row number, cell texts)


# ------------------------------------------------------------- formatting ---
def fmt_value(value: Any) -> str:
    """Render one cell value as short, unambiguous text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return str(int(value))
        return format(value, ".12g")  # hide float noise like 0.30000000000000004
    if isinstance(value, dt.datetime):
        if value.time() == dt.time(0):
            return value.date().isoformat()
        return value.isoformat(sep=" ", timespec="seconds").removesuffix(":00")
    if isinstance(value, dt.time):
        return value.isoformat(timespec="seconds").removesuffix(":00")
    if isinstance(value, (dt.date, int)):
        return str(value)
    return " ".join(str(value).split())  # collapse newlines/runs of spaces


def used_cols(rows: list[Row]) -> list[int]:
    """Indexes of the columns that hold data in any of the given rows."""
    width = max((len(c) for _, c in rows), default=0)
    return [i for i in range(width) if any(i < len(c) and c[i] for _, c in rows)]


def filled(cells: list[str], cols: list[int]) -> int:
    return sum(1 for i in cols if i < len(cells) and cells[i])


def render_table(rows: list[Row]) -> str:
    cols = used_cols(rows)

    def line(cells: list[str]) -> str:
        picked = [cells[i] if i < len(cells) else "" for i in cols]
        return "|" + "|".join(c.replace("|", "\\|") for c in picked) + "|"

    header = list(rows[0][1])
    header += [""] * (cols[-1] + 1 - len(header))
    # name blank header cells after their column so every column has a label
    header = [h or get_column_letter(i + 1) for i, h in enumerate(header)]
    out = [line(header), "|" + "|".join("---" for _ in cols) + "|"]
    out += [line(cells) for _, cells in rows[1:]]
    return "\n".join(out)


def render_cells(rows: list[Row]) -> str:
    """One line per row, each non-empty cell as its A1-style reference."""
    return "\n".join(
        " | ".join(f"{get_column_letter(i + 1)}{n}: {v}" for i, v in enumerate(cells) if v)
        for n, cells in rows
    )


def render_csv(rows: list[Row]) -> str:
    cols = used_cols(rows)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for _, cells in rows:
        writer.writerow([cells[i] if i < len(cells) else "" for i in cols])
    return "```csv\n" + buf.getvalue() + "```"


def split_table(rows: list[Row]) -> int | None:
    """auto mode: index of the row that starts a dense table, if there is one.

    Rows above it (titles, "Customer: ..." fields) are better as a cell list;
    a sheet with no dense block is a form and stays a cell list entirely.
    """
    cols = used_cols(rows)
    for h, (_, cells) in enumerate(rows[:-1]):
        if filled(cells, cols) < max(min(2, len(cols)), (len(cols) + 1) // 2):
            continue  # too empty to be a header row
        body = rows[h:]
        body_cols = used_cols(body)
        cells_used = sum(filled(c, body_cols) for _, c in body)
        if cells_used / (len(body) * len(body_cols)) >= SPARSE_DENSITY:
            return h
        return None
    return None


def render_sheet(
    title: str, rows: list[Row], mode: str, max_rows: int, hidden: bool = False
) -> str:
    rows = [(n, cells) for n, cells in rows if any(cells)]
    label = f"## {title}" + (" (hidden)" if hidden else "")
    if not rows:
        return f"{label}\n(empty)"

    cols = used_cols(rows)
    span = (
        f"{get_column_letter(cols[0] + 1)}{rows[0][0]}:"
        f"{get_column_letter(cols[-1] + 1)}{rows[-1][0]}"
    )
    meta = f"{label} [{span}]"

    # (pre, table): rows rendered as a cell list, then rows rendered as a table
    if mode == "cells":
        pre, table = rows, []
    elif mode in ("table", "csv"):
        pre, table = [], rows
    else:  # auto
        h = split_table(rows)
        pre, table = (rows, []) if h is None else (rows[:h], rows[h:])

    if max_rows:
        part = "table" if table else "cell"
        data = len(table) - 1 if table else len(pre)
        if data > max_rows:
            meta += f" (first {max_rows} of {data} {part} rows)"
            if table:
                table = table[: max_rows + 1]  # +1 keeps the header row
            else:
                pre = pre[:max_rows]

    blocks = [render_cells(pre)] if pre else []
    if table:
        blocks.append(render_csv(table) if mode == "csv" else render_table(table))
    body = "\n\n".join(blocks)
    return f"{meta}\n{body}"


# ---------------------------------------------------------------- readers ---
def iter_xlsx(stream: BinaryIO, formulas: bool) -> Iterable[tuple[str, list[Row], bool]]:
    import openpyxl

    data = stream.read()
    values_wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    # second pass for formulas: shown when asked, or when Excel never cached a
    # value (files written by scripts), so the cell isn't silently lost
    formula_wb = openpyxl.load_workbook(io.BytesIO(data), data_only=False, read_only=True)
    try:
        for ws in values_wb.worksheets:
            if not hasattr(ws, "iter_rows"):
                continue  # chartsheet
            fws = formula_wb[ws.title]
            ws.reset_dimensions()  # don't trust the stored used-range
            fws.reset_dimensions()
            rows: list[Row] = []
            pairs = zip(ws.iter_rows(values_only=True), fws.iter_rows(values_only=True))
            for n, (vals, forms) in enumerate(pairs, start=1):
                cells = []
                for v, f in zip(vals, forms):
                    text = fmt_value(v)
                    if isinstance(f, str) and f.startswith("="):
                        if not text:
                            text = f
                        elif formulas:
                            text = f"{text} ({f})"
                    cells.append(text)
                rows.append((n, cells))
            yield ws.title, rows, getattr(ws, "sheet_state", "visible") != "visible"
    finally:
        values_wb.close()
        formula_wb.close()


def iter_xls(stream: BinaryIO) -> Iterable[tuple[str, list[Row], bool]]:
    import xlrd

    book = xlrd.open_workbook(file_contents=stream.read())
    for sh in book.sheets():
        rows: list[Row] = []
        for r in range(sh.nrows):
            cells = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                v = cell.value
                if cell.ctype == xlrd.XL_CELL_DATE:
                    v = xlrd.xldate.xldate_as_datetime(v, book.datemode)
                elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                    v = bool(v)
                elif cell.ctype in (xlrd.XL_CELL_ERROR, xlrd.XL_CELL_BLANK):
                    v = None
                cells.append(fmt_value(v))
            rows.append((r + 1, cells))
        yield sh.name, rows, sh.visibility != 0


def iter_csv(stream: BinaryIO, charset: str | None) -> Iterable[tuple[str, list[Row], bool]]:
    data = stream.read()
    for enc in [charset, "utf-8-sig", "cp1252", "latin-1"]:
        if not enc:
            continue
        try:
            text = data.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [(n, [fmt_value(c) for c in r]) for n, r in enumerate(reader, start=1)]
    yield "CSV", rows, False


# -------------------------------------------------------------- converter ---
class LeanSheetConverter(DocumentConverter):
    """markitdown converter; options come in as convert() kwargs:

    sheet_mode (auto|table|cells|csv), sheet_formulas (bool), sheet_max_rows (int)
    """

    def accepts(self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any) -> bool:
        return (stream_info.extension or "").lower() in SHEET_EXTENSIONS

    def convert(
        self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any
    ) -> DocumentConverterResult:
        mode = kwargs.get("sheet_mode", "auto")
        max_rows = int(kwargs.get("sheet_max_rows") or 0)
        ext = (stream_info.extension or "").lower()
        if ext == ".xls":
            sheets = iter_xls(file_stream)
        elif ext == ".csv":
            sheets = iter_csv(file_stream, stream_info.charset)
        else:
            sheets = iter_xlsx(file_stream, bool(kwargs.get("sheet_formulas")))
        parts = [
            render_sheet(title, rows, mode, max_rows, hidden)
            for title, rows, hidden in sheets
        ]
        return DocumentConverterResult(markdown="\n\n".join(parts))
