"""Bounded CSV response projection for deterministic content assertions; cells stay strings."""

import csv
import io


def csv_table(content: bytes) -> dict | None:
    """Return a table only for unambiguous UTF-8 CSV; never execute or persist cell contents."""
    try:
        reader = csv.reader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
        columns = next(reader)
        if (not 0 < len(columns) <= 128 or len(set(columns)) != len(columns)
                or any(not name or len(name) > 128 or any(ord(c) < 32 for c in name)
                       for name in columns)):
            return None
        rows = []
        for cells in reader:
            if len(rows) >= 1000 or len(cells) != len(columns):
                return None
            rows.append(dict(zip(columns, cells)))
        return dict(columns=columns, rows=rows, row_count=len(rows))
    except (UnicodeError, csv.Error, StopIteration):
        return None
