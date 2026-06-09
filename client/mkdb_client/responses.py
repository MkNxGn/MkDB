"""
MkDB SDK response objects.

Every public client method returns one of these instead of a raw dict,
giving callers full IDE autocompletion and type-checking.

Entity data (the actual record fields) is intentionally left as plain
``dict`` — no generated model classes.
"""

from __future__ import annotations
from typing import Any, Optional, Union


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
            if name in self.__dict__ and isinstance(self.__dict__[name], base_object):
                self.__dict__[name].update(item)
            else:
                setattr(self, name, item)

    def __repr__(self) -> str:
        return str(self.json)


class SnapshotBaseObject(base_object):
    """
    Tracked record object. Maintains a snapshot of original values to calculate 
    deltas. Sends only updated paths via .patch().
    """
    def __init__(self, data: dict = {}, store: str = "", record_id: str = "", client: Any = None):
        self.__store__    = store
        self.__id__       = record_id
        self.__client__   = client
        self.__original__ = {}
        super().__init__(data)
        # Snapshot the flattened state after initial update
        self.__original__ = self._flatten(self.json)

    def bind(self, store: str, record_id: str, client: Any):
        """Bind the object to a specific store and client for patching."""
        self.__store__ = store
        self.__id__    = record_id
        self.__client__ = client
        # Re-snapshot to ensure we are starting fresh from whatever data is currently in the object
        self.__original__ = self._flatten(self.json)
        return self

    def _flatten(self, data: dict, prefix: str = "") -> dict:
        res = {}
        for k, v in data.items():
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                res.update(self._flatten(v, f"{key}."))
            else:
                res[key] = v
        return res

    def patch(self) -> WriteResponse | None:
        """Sends only the changed fields back to the server as a delta update."""
        if not self.__client__ or not self.__store__ or not self.__id__:
            raise ValueError("Object is not bound to a client/store/id. Cannot patch.")
            
        current_flat = self._flatten(self.json)
        delta = {}
        
        # Detect Updates/Additions
        for k, v in current_flat.items():
            if k not in self.__original__ or self.__original__[k] != v:
                delta[k] = v
        
        # Detect Deletions
        for k in self.__original__:
            if k not in current_flat:
                delta[k] = None
        
        if not delta:
            return None
            
        resp = self.__client__.set(self.__store__, self.__id__, delta)
        # Reset snapshot to current state
        self.__original__ = current_flat
        return resp


class ListStoresResponse(base_object):
    """Returned by ``client.list_stores()``."""

    def __init__(self, stores: list[dict]):
        self.stores: list[dict] = stores
        super().__init__({})


# ---------------------------------------------------------------------------
# Response classes
# ---------------------------------------------------------------------------


class GetResponse(base_object):
    """Returned by ``client.get()``."""

    def __init__(self, record_id: str, data: Any, found: bool):
        self.record_id: str = record_id
        self.data:      Any = data   # dict, SnapshotBaseObject, or None
        self.found:     bool = found
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

    def __init__(self, count: int, total_matches: int, ids: list,
                 records: Optional[list] = None,
                 store: str = ""):
        self.count:         int            = count          # visible on this page
        self.total_matches: int            = total_matches  # total in database
        self.ids:           list           = ids
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
