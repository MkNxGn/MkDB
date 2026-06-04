"""
MkDB SDK response objects.

Every public client method returns one of these instead of a raw dict,
giving callers full IDE autocompletion and type-checking.

Entity data (the actual record fields) is intentionally left as plain
``dict`` — no generated model classes.
"""

from __future__ import annotations
from typing import Optional, Union


# ---------------------------------------------------------------------------
# Minimal base_object — copied from src/objects so the SDK stays standalone.
# Provides .json and .update() exactly as the server-side version does.
# ---------------------------------------------------------------------------

def _format_items(input: Union[dict, list]) -> Union[dict, list]:
    if type(input) == list:
        data = []
        for item in input:
            if isinstance(item, base_object):
                data.append(item.json)
            elif type(item) in [list, dict]:
                data.append(_format_items(item))
            elif type(item) in [tuple, int, float, str, bool]:
                data.append(item)
        return data
    elif type(input) == dict:
        data = {}
        for key in input:
            if "__" in str(key):
                continue
            item = input[key]
            if isinstance(item, base_object):
                data[key] = item.json
            elif type(item) in [list, dict]:
                data[key] = _format_items(item)
            elif type(item) in [tuple, int, float, str, bool]:
                data[key] = item
        return data
    return input


class base_object:
    def __init__(self, data: dict = {}):
        self.update(data)

    def get(self, name, default=None):
        return self.__dict__.get(name, default)

    @property
    def json(self) -> dict:
        data = {}
        for name in self.__dict__:
            if "__" in name:
                continue
            item = self.__dict__[name]
            if isinstance(item, base_object):
                data[name] = item.json
            elif type(item) in [list, dict]:
                data[name] = _format_items(item)
            elif type(item) in [tuple, int, float, str, bool]:
                data[name] = item
        return data

    def update(self, data: dict):
        if not isinstance(data, dict):
            return
        for name, item in data.items():
            if name in self.__dict__:
                if isinstance(self.__dict__[name], base_object):
                    self.__dict__[name].update(item)
                elif type(item) in [list, int, float, str, dict, bool]:
                    setattr(self, name, item)

    def __repr__(self) -> str:
        return str(self.json)


# ---------------------------------------------------------------------------
# Response classes
# ---------------------------------------------------------------------------


class GetResponse(base_object):
    """Returned by ``client.get()``."""

    def __init__(self, record_id: str, data: Optional[dict], found: bool):
        self.record_id: str            = record_id
        self.data:      Optional[dict] = data   # flat field dict, or None
        self.found:     bool           = found
        super().__init__({})

    def __bool__(self) -> bool:
        return self.found


class WriteResponse(base_object):
    """Returned by ``client.set()`` and ``client.insert()``."""

    def __init__(self, record_id: str, store: str):
        self.record_id: str = record_id
        self.store:     str = store
        super().__init__({})


class DeleteResponse(base_object):
    """Returned by ``client.delete()``."""

    def __init__(self, record_id: str, store: str):
        self.record_id: str = record_id
        self.store:     str = store
        super().__init__({})


class QueryResponse(base_object):
    """Returned by ``client.query()``."""

    def __init__(self, count: int, ids: list,
                 records: Optional[list] = None,
                 store: str = ""):
        self.count:   int            = count
        self.ids:     list           = ids
        # Only populated when hydrate=True
        self.records: Optional[list] = records
        self.store:   str            = store
        super().__init__({})

    def __len__(self) -> int:
        return self.count

    def __bool__(self) -> bool:
        return self.count > 0

    def __iter__(self):
        """Iterate over records if hydrated, otherwise over IDs."""
        if self.records is not None:
            return iter(self.records)
        return iter(self.ids)


class GenerateIdResponse(base_object):
    """Returned by ``client.generate_id()``."""

    def __init__(self, record_id: str, store: str):
        self.record_id: str = record_id
        self.store:     str = store
        super().__init__({})

    def __str__(self) -> str:
        return self.record_id
