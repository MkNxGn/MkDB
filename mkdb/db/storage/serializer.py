"""
Record serialization helpers for .log file lines.

Wire format:  {record_id} {json_object}
One line per record: record_id (no spaces) followed by a single space,
then a compact JSON object.  Native JSON types (str, int, float, bool, list,
dict, None) are preserved exactly — no custom encoding required.
"""

import json


def serialize_record(record_id: str, flat_dict: dict) -> str:
    """
    Produce a single log line string (no trailing newline).
    Example: abc123 {"name": "Gizmo", "price": 19.99, "in_stock": true}
    """
    return f"{record_id} {json.dumps(flat_dict, ensure_ascii=False)}"


def deserialize_record(line: str) -> tuple:
    """
    Parse a log line into (record_id, flat_dict).
    Strips trailing newline/whitespace before parsing.
    Returns (None, {}) on empty or malformed lines.
    """
    line = line.strip()
    if not line:
        return (None, {})
    space = line.find(" ")
    if space == -1:
        return (None, {})
    record_id = line[:space]
    try:
        flat_dict = json.loads(line[space + 1:])
    except json.JSONDecodeError:
        return (None, {})
    return (record_id, flat_dict)
