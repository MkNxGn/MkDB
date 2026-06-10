"""
QueryEngine — declarative dict query dispatcher for one store.

Query syntax (from the plan's Query Syntax Specification):
  {"id": "x"}                       → fast-path primary index lookup
  {"id": ["x", "y"]}                → multi-ID fetch
  {"field": "exact string"}         → full-scan exact text match
  {"field": ["kw1", "kw2"]}         → FullTextIndex AND search
  {"field": 2010}                   → NumericIndex exact_query
  {"field": {">": 1999, "<": 2015}} → NumericIndex range_query
  Compound dict → AND-intersect all candidate sets
"""

import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

OPERATORS = {">=", "<=", ">", "<", "eq", "neq", "in", "is_included", "nin", "not_included", "contains", "exists"}

# Friendly aliases accepted from SDK callers
_OP_ALIASES = {
    "gte": ">=", 
    "lte": "<=", 
    "gt": ">", 
    "lt": "<",
    "is_included": "in",
    "not_included": "nin"
}


class QuerySyntaxError(ValueError):
    pass


class QueryEngine:
    def __init__(self, store):
        """
        Parameters
        ----------
        store : src.db.objects.store.store
            The owning store instance (used for read() and index_manager).
        """
        from mkdb.db.objects.store import store as _store
        self._store:_store = store
        self._full_text_indexes: dict = {}   # field_name -> FullTextIndex
        self._numeric_indexes:   dict = {}   # field_name -> NumericIndex
        self.__rebuild_locks__ = {}     # field_name -> threading.Lock to prevent concurrent rebuilds of the same index

    # ------------------------------------------------------------------
    # Index lifecycle
    # ------------------------------------------------------------------

    def build_indexes(self) -> None:
        """
        Instantiate indexes for all fields marked queryable in schema_config.
        Called from store.setup() after storage is ready.

        If an index file does not exist on disk, the store is scanned and the
        index is built and saved immediately so it survives the next restart.
        Fields whose index file exceeds ram_config.index_ram_threshold_bytes are
        kept on disk only — queries read from the file; mutations are buffered
        and flushed before each query.
        """
        from mkdb.db.query.full_text_index import FullTextIndex
        from mkdb.db.query.numeric_index import NumericIndex

        schema = getattr(self._store.config, "schema_config", None)
        if schema is None:
            return

        ram_cfg = getattr(self._store.config, "ram_config", None)
        threshold = getattr(ram_cfg, "index_ram_threshold_bytes", 0) if ram_cfg else 0

        store_name = self._store.config.name
        logger.info("[%s] build_indexes: checking %d schema field(s)...", store_name, len(schema.fields))

        needs_rebuild: list = []
        for field_name, field_schema in schema.fields.items():
            q = getattr(field_schema, "queryable", False)
            if not q:
                continue
            if q in ("full-text", True):
                idx = FullTextIndex(
                    store_path=self._store.store_path,
                    service=self._store.config.name,
                    field=field_name,
                    ram_threshold_bytes=threshold,
                )
                self._full_text_indexes[field_name] = idx
                if not os.path.exists(idx.path) or os.path.getsize(idx.path) == 0:
                    needs_rebuild.append(field_name)
            elif q == "numeric":
                idx = NumericIndex(
                    store_path=self._store.store_path,
                    service=self._store.config.name,
                    field=field_name,
                    ram_threshold_bytes=threshold,
                )
                self._numeric_indexes[field_name] = idx
                if not os.path.exists(idx.path) or os.path.getsize(idx.path) == 0:
                    needs_rebuild.append(field_name)

        if needs_rebuild:
            store_name = self._store.config.name
            logger.info("[%s] Index files missing for: %s — rebuilding from stored data...", store_name, needs_rebuild)
            for field_name in needs_rebuild:
                try:
                    self.rebuild_index(field_name)
                except Exception as exc:
                    logger.warning("build_indexes: rebuild_index('%s') failed: %s", field_name, exc)
        else:
            logger.info("[%s] All index files present — loaded from disk.", self._store.config.name)

        if getattr(self._store.config, "schema_auto_discover_on_boot", False):
            self.discover_fields()

    def on_write(self, record_id: str, flat_delta: dict) -> None:
        """Update all relevant indexes when a record is written."""
        if getattr(self._store.config, "schema_auto_discover_on_write", False):
            self._autodiscover_fields(flat_delta)

        for field, idx in self._full_text_indexes.items():
            if field in flat_delta:
                idx.add(record_id, str(flat_delta[field]))
        for field, idx in self._numeric_indexes.items():
            if field in flat_delta:
                try:
                    idx.add(record_id, flat_delta[field])
                except (ValueError, TypeError):
                    pass

    def discover_fields(self) -> int:
        """Scan all stored records and add any unseen fields to schema_config.

        Returns the number of newly-discovered fields.
        Called automatically on boot when schema_auto_discover_on_boot is True,
        on write when schema_auto_discover_on_write is True, or manually via the
        control-panel "Find New Fields" button.
        """
        from mkdb.db.storage import serializer as _ser

        store = self._store
        if store.index_manager is None or store.log_manager is None:
            return 0

        schema = getattr(store.config, "schema_config", None)
        if schema is None:
            return 0

        found: set[str] = set()
        for record_id in store.index_manager.all_record_ids():
            entry = store.index_manager.get(record_id)
            if entry is None:
                continue
            seg, offset, size = entry
            try:
                line = store.log_manager.read(seg, offset, size)
                _, flat = _ser.deserialize_record(line)
                for field_name in flat:
                    if field_name not in schema.fields:
                        found.add(field_name)
            except Exception as exc:
                logger.warning("discover_fields: skipping %s: %s", record_id, exc)

        if found:
            from mkdb.config.db import field_schema as FieldSchema
            for field_name in found:
                fs = FieldSchema({})
                fs.name = field_name
                fs.queryable = False
                fs.indexed = False
                schema.fields[field_name] = fs
            try:
                store.db.config.save()
            except Exception as exc:
                logger.warning("discover_fields: config save failed: %s", exc)
            logger.info("discover_fields: added %d new field(s): %s", len(found), sorted(found))

        return len(found)

    def _autodiscover_fields(self, flat_delta: dict) -> None:
        """Add any previously-unseen fields from a single write delta to schema_config."""
        from mkdb.config.db import field_schema as FieldSchema

        schema = getattr(self._store.config, "schema_config", None)
        if schema is None:
            return

        new_fields = [f for f in flat_delta if f not in schema.fields]
        if not new_fields:
            return

        for field_name in new_fields:
            fs = FieldSchema({})
            fs.name = field_name
            fs.queryable = False
            fs.indexed = False
            schema.fields[field_name] = fs

        try:
            self._store.db.config.save()
        except Exception as exc:
            logger.warning("_autodiscover_fields: config save failed: %s", exc)

    def on_delete(self, record_id: str, flat_dict: dict) -> None:
        """Remove a record from all indexes."""
        for field, idx in self._full_text_indexes.items():
            if field in flat_dict:
                idx.remove(record_id, str(flat_dict[field]))
        for field, idx in self._numeric_indexes.items():
            if field in flat_dict:
                try:
                    idx.remove(record_id, flat_dict[field])
                except (ValueError, TypeError):
                    pass

    def save_all(self) -> None:
        """Persist all in-memory indexes to disk."""
        for idx in self._full_text_indexes.values():
            idx.save()
        for idx in self._numeric_indexes.values():
            idx.save()

    def rebuild_index(self, field_name: str) -> None:
        """
        Backfill the index for a single field by scanning all log segments.
        Safe to call from a background thread — reads only, no writes to log.
        """
        from mkdb.db.query.full_text_index import FullTextIndex
        from mkdb.db.query.numeric_index import NumericIndex
        from mkdb.db.storage import serializer as _ser

        store = self._store
        if store.log_manager is None or store.index_manager is None:
            raise RuntimeError("Store storage not initialised.")

        # Determine index type from schema_config
        schema = getattr(store.config, "schema_config", None)
        if schema is None or field_name not in schema.fields:
            raise QuerySyntaxError(f"Field '{field_name}' not in schema_config.")
        field_schema = schema.fields[field_name]
        q = getattr(field_schema, "queryable", False)

        # Determine RAM threshold from store config
        ram_cfg = getattr(store.config, "ram_config", None)
        threshold = getattr(ram_cfg, "index_ram_threshold_bytes", 0) if ram_cfg else 0

        # Build a fresh in-memory index (start empty, ignoring any stale file)
        if q in ("full-text", True):
            new_idx = FullTextIndex(store.store_path, store.config.name, field_name,
                                    ram_threshold_bytes=threshold)
            new_idx._map = {}
            new_idx._in_ram = True          # force RAM mode while backfilling
            self._full_text_indexes[field_name] = new_idx
        elif q == "numeric":
            new_idx = NumericIndex(store.store_path, store.config.name, field_name,
                                   ram_threshold_bytes=threshold)
            new_idx._map = {}
            new_idx._values = []
            new_idx._in_ram = True          # force RAM mode while backfilling
            self._numeric_indexes[field_name] = new_idx
        else:
            raise QuerySyntaxError(f"Field '{field_name}' is not queryable.")

        # Scan all live records
        record_ids = store.index_manager.all_record_ids()
        logger.info("[%s] rebuild_index('%s'): scanning %d live records...", store.config.name, field_name, len(record_ids))
        for record_id in record_ids:
            entry = store.index_manager.get(record_id)
            if entry is None:
                continue
            seg, offset, size = entry
            try:
                line = store.log_manager.read(seg, offset, size)
                rid, flat = _ser.deserialize_record(line)
                if rid is None:
                    logger.warning("rebuild_index: could not deserialize record at %s:%d:%d", seg, offset, size)
                    continue
                if field_name in flat:
                    val = flat[field_name]
                    if val is not None:
                        new_idx.add(record_id, str(val))
            except Exception as exc:
                logger.warning("rebuild_index: skipping %s: %s", record_id, exc)

        new_idx.save()
        n = len(new_idx._map)
        logger.info("[%s] Index for '%s' rebuilt: %d entries — saved to %s", store.config.name, field_name, n, new_idx.path)
        logger.info("rebuild_index: field '%s' rebuilt (%d entries)", field_name, n)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(self, query_payload: dict) -> dict:
        """
        Execute a query payload and return results with metadata.

        payload = {
            "filter": { ... },
            "sort": "+field" | "-field",
            "limit": int,
            "offset": int
        }
        """
        if not isinstance(query_payload, dict):
            raise QuerySyntaxError("Query must be a dict")
        
        # Backward compatibility: if "filter" is not present, treat whole dict as filter
        filter_dict = query_payload.get("filter", query_payload) if "filter" in query_payload or not query_payload else query_payload
        
        # If the user passed top-level keys like sort/limit but also mixed them in the filter, 
        # we need to be careful. Let's assume standardized format if "filter" is present.
        if "filter" in query_payload:
            sort_key = query_payload.get("sort")
            limit = query_payload.get("limit")
            offset = query_payload.get("offset")
        else:
            # Check if they are just passing a filter directly (legacy)
            # but we'll strip our reserved words just in case they are at top level
            sort_key = filter_dict.pop("sort", None)
            limit = filter_dict.pop("limit", None)
            offset = filter_dict.pop("offset", None)

        store_name = self._store.config.name
        
        # 1. Gather candidates (Logical AND is default at top level)
        if not filter_dict:
            idx_mgr = self._store.index_manager
            candidates = set(idx_mgr.all_record_ids()) if idx_mgr else set()
        else:
            candidates = self._eval_logic_block("$and", filter_dict)

        total_matches = len(candidates)
        results = list(candidates)

        # 2. Sort
        if sort_key and results:
            reverse = sort_key.startswith("-")
            field = sort_key.lstrip("+-")
            
            # Helper to get sort value
            def get_val(rid):
                rec = self._store.read(rid)
                v = rec.get(field) if rec else None
                return (v is not None, v) # keep None values at bottom

            results.sort(key=get_val, reverse=reverse)

        # 3. Paginate
        start = int(offset) if offset is not None else 0
        
        # Determine actual limit to use
        actual_limit = limit
        if actual_limit is None:
            # Fallback to store config default
            actual_limit = getattr(self._store.config, "default_query_limit", 300)
            if actual_limit <= 0:
                actual_limit = None # Treat 0 as unlimited fallback

        if actual_limit is not None:
            end = start + int(actual_limit)
            results = results[start:end]
        elif start > 0:
            results = results[start:]

        return {
            "ids": results,
            "count": len(results),
            "total_matches": total_matches
        }

    def _eval_logic_block(self, logic_op: str, val: Any) -> set:
        """Evaluate $and, $or, or a standard filter dict."""
        idx_mgr = self._store.index_manager
        if not idx_mgr: return set()

        if logic_op == "$or":
            if not isinstance(val, list):
                raise QuerySyntaxError("$or requires a list of filters")
            res = set()
            for sub_filter in val:
                res |= self._eval_logic_block("$and", sub_filter)
            return res
        
        if logic_op == "$and":
            # If it's a list, intersect them
            if isinstance(val, list):
                if not val: return set(idx_mgr.all_record_ids())
                res = self._eval_logic_block("$and", val[0])
                for sub in val[1:]:
                    res &= self._eval_logic_block("$and", sub)
                return res
            
            # If it's a dict, handle each field entry (standard behavior)
            if isinstance(val, dict):
                candidate_sets = []
                for field, field_val in val.items():
                    if field == "$or":
                        candidate_sets.append(self._eval_logic_block("$or", field_val))
                    elif field == "$and":
                        candidate_sets.append(self._eval_logic_block("$and", field_val))
                    else:
                        candidate_sets.append(self._eval_field(field, field_val))
                
                if not candidate_sets:
                    return set(idx_mgr.all_record_ids())
                
                res = candidate_sets[0]
                for s in candidate_sets[1:]:
                    res &= s
                return res

        return set()

    def _eval_field(self, field: str, value) -> set:
        """Evaluate one field clause and return a set of matching record IDs."""
        idx_mgr = self._store.index_manager
        if not idx_mgr: return set()

        # Handle simplified syntax (exact match)
        if isinstance(value, (int, float)):
            idx = self._numeric_indexes.get(field)
            return idx.exact_query(value) if idx else self._full_scan_filter(field, value)

        if isinstance(value, str):
            # Check if it looks like a simple string match
            return self._text_match(field, value)

        if isinstance(value, list):
            # Legacy/Shortcut: list of strings -> FullText AND search
            idx = self._full_text_indexes.get(field)
            if idx: return idx.search(value, mode="and")
            return self._full_scan_filter(field, value, op="in")

        if isinstance(value, dict):
            # Rich operators
            res = set(idx_mgr.all_record_ids())
            for op, op_val in value.items():
                op = _OP_ALIASES.get(op, op)
                
                if op in (">", ">=", "<", "<="):
                    idx = self._numeric_indexes.get(field)
                    if idx:
                        lo = op_val if op.startswith(">") else None
                        hi = op_val if op.startswith("<") else None
                        inc = "=" in op
                        # Note: This simple mapping only works for single ops. 
                        # Range query handled later if multiple numeric ops present.
                        # For now, let's just do a basic intersect.
                        res &= idx.range_query(
                            lo=lo if op.startswith(">") else None,
                            hi=hi if op.startswith("<") else None,
                            lo_inclusive=inc if op.startswith(">") else True,
                            hi_inclusive=inc if op.startswith("<") else True
                        )
                    else:
                        res &= self._full_scan_filter(field, op_val, op)

                elif op == "exists":
                    def exists_check(v):
                        return v is not None and v != "" and v != []
                    res &= self._full_scan_filter(field, op_val, op="custom", fn=exists_check if op_val else lambda v: not exists_check(v))

                elif op == "in" or op == "is_included":
                    res &= self._full_scan_filter(field, op_val, op="in")
                
                elif op == "nin" or op == "not_included":
                    res &= self._full_scan_filter(field, op_val, op="nin")

                elif op == "contains":
                    ft_idx = self._full_text_indexes.get(field)
                    if ft_idx:
                        from mkdb.db.query.tokenizer import tokenize as _tok
                        res &= ft_idx.search(_tok(op_val), mode="and")
                    else:
                        res &= self._full_scan_filter(field, op_val, op="contains")

                elif op == "eq":
                    res &= self._full_scan_filter(field, op_val, op="eq")
                
                elif op == "neq":
                    res &= self._full_scan_filter(field, op_val, op="neq")

            return res

        return set()

    def _full_scan_filter(self, field: str, target: Any, op: str = "eq", fn=None) -> set:
        """Last resort: read records and check values manually."""
        idx_mgr = self._store.index_manager
        all_ids = idx_mgr.all_record_ids() if idx_mgr else []
        res = set()

        nested_enabled = getattr(self._store.config, "nested_queries_enabled", False)
        is_nested = nested_enabled and "." in field

        def get_val(data):
            if not data: return None
            if not is_nested:
                return data.get(field)
            # Traverse dots
            curr = data
            for part in field.split("."):
                if isinstance(curr, dict) and part in curr:
                    curr = curr[part]
                else:
                    return None
            return curr

        for rid in all_ids:
            rec = self._store.read(rid)
            val = get_val(rec)
            
            if op == "eq" and val == target: res.add(rid)
            elif op == "neq" and val != target: res.add(rid)
            elif op == "in" and val in target: res.add(rid)
            elif op == "nin" and val not in target: res.add(rid)
            elif op == "contains" and isinstance(val, str) and target.lower() in val.lower(): res.add(rid)
            elif op == ">" and val is not None and val > target: res.add(rid)
            elif op == ">=" and val is not None and val >= target: res.add(rid)
            elif op == "<" and val is not None and val < target: res.add(rid)
            elif op == "<=" and val is not None and val <= target: res.add(rid)
            elif op == "custom" and fn and fn(val): res.add(rid)
        return res

    def _text_match(self, field: str, value: str) -> set:
        """Handles string matching with FT index optimization if available."""
        ft_idx = self._full_text_indexes.get(field)
        if not ft_idx:
            return self._full_scan_filter(field, value, "eq")
        
        # Intersection search to narrow candidates
        from mkdb.db.query.tokenizer import tokenize as _tok
        stems = _tok(value)
        if not stems: return set(self._store.index_manager.all_record_ids())
        
        candidates = ft_idx.search(stems, mode="and")
        res = set()
        for rid in candidates:
            rec = self._store.read(rid)
            if rec and rec.get(field) == value:
                res.add(rid)
        return res
