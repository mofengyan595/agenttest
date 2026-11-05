"""Bounded CSV response projection for deterministic content assertions; cells stay strings."""

import csv
import io

from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.selectors import TOKEN


def valid_csv_columns(columns):
    return (isinstance(columns, list) and 0 < len(columns) <= 128
            and all(isinstance(name, str) and 0 < len(name) <= 128
                    and all(ord(c) >= 32 for c in name) for name in columns)
            and len(set(columns)) == len(columns))


def csv_table(content: bytes) -> dict | None:
    """Return a table only for unambiguous UTF-8 CSV; never execute or persist cell contents."""
    try:
        reader = csv.reader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
        columns = next(reader)
        if not valid_csv_columns(columns):
            return None
        rows = []
        for cells in reader:
            if len(rows) >= 1000 or len(cells) != len(columns):
                return None
            rows.append(dict(zip(columns, cells)))
        return dict(columns=columns, rows=rows, row_count=len(rows))
    except (UnicodeError, csv.Error, StopIteration):
        return None


def validate_csv_assertions(operation, statuses, checks):
    """Preflight cell selectors against the operator-confirmed exact header assertion."""
    if not any("text/csv" in operation.responses.get(str(status), {}).get("content", {})
               for status in statuses):
        return
    cells = [(check.select, tokens[2][0]) for check in checks
             if len(tokens := TOKEN.findall(check.select)) >= 3 and tokens[0] == ("rows", "")
             and tokens[1][1] and tokens[2][0]]
    if not cells:
        return
    headers = [check.expected for check in checks
               if check.select == "$.columns" and check.operator == "equals"]
    if (not headers or not all(valid_csv_columns(header) and header == headers[0]
                              for header in headers)):
        error = ValidationFailure(Layer.POLICY, "CSV_HEADER_EVIDENCE_REQUIRED")
        error.diagnostic.fields = [operation.operation_id, cells[0][0]]
        raise error
    for path, column in cells:
        if column not in headers[0]:
            error = ValidationFailure(Layer.POLICY, "CSV_ASSERTION_COLUMN_UNKNOWN")
            error.diagnostic.fields = [operation.operation_id, path]
            raise error
