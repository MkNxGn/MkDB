"""
MkDB Query Builder — a fluent interface for building complex MkDB queries.
"""

from __future__ import annotations
from typing import Any, Optional, Dict, List, Union

class Q:
    def __init__(self, filter_dict: Optional[Dict[str, Any]] = None):
        self._filter: Dict[str, Any] = filter_dict or {}
        self._sort: Optional[str] = None
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None

    @classmethod
    def field(cls, name: str) -> FieldProxy:
        """Start a query for a specific field."""
        return FieldProxy(name)

    def sort(self, field_with_prefix: str) -> Q:
        """Set sorting, e.g., .sort("-price") or .sort("+name")"""
        self._sort = field_with_prefix
        return self

    def limit(self, count: int) -> Q:
        """Set result limit."""
        self._limit = count
        return self

    def offset(self, count: int) -> Q:
        """Set result offset."""
        self._offset = count
        return self

    def __and__(self, other: Q) -> Q:
        """Combine two queries with AND logic."""
        return Q({"$and": [self._filter, other._filter]})

    def __or__(self, other: Q) -> Q:
        """Combine two queries with OR logic."""
        return Q({"$or": [self._filter, other._filter]})

    def build(self) -> Dict[str, Any]:
        """Returns the final query dictionary for use in client.query()."""
        q: Dict[str, Any] = {"filter": self._filter}
        if self._sort: q["sort"] = self._sort
        if self._limit: q["limit"] = self._limit
        if self._offset: q["offset"] = self._offset
        return q

class FieldProxy:
    def __init__(self, name: str):
        self._name = name

    def eq(self, val: Any) -> Q: return Q({self._name: {"eq": val}})
    def neq(self, val: Any) -> Q: return Q({self._name: {"neq": val}})
    def gt(self, val: Union[int, float]) -> Q: return Q({self._name: {"gt": val}})
    def gte(self, val: Union[int, float]) -> Q: return Q({self._name: {"gte": val}})
    def lt(self, val: Union[int, float]) -> Q: return Q({self._name: {"lt": val}})
    def lte(self, val: Union[int, float]) -> Q: return Q({self._name: {"lte": val}})
    def contains(self, val: str) -> Q: return Q({self._name: {"contains": val}})
    def is_included(self, vals: List[Any]) -> Q: return Q({self._name: {"in": vals}})
    def exists(self, val: bool = True) -> Q: return Q({self._name: {"exists": val}})
