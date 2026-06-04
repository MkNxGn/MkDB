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

logger = logging.getLogger(__name__)

OPERATORS = {">=", "<=", ">", "<"}

# Friendly aliases accepted from SDK callers
_OP_ALIASES = {"gte": ">=", "lte": "<=", "gt": ">", "lt": "<"}


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
        self._store = store
        self._full_text_indexes: dict = {}   # field_name -> FullTextIndex
        self._numeric_indexes:   dict = {}   # field_name -> NumericIndex

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
        print(f"[{store_name}] build_indexes: checking {len(schema.fields)} schema field(s)...")

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
                if not os.path.exists(idx.path):
                    needs_rebuild.append(field_name)
            elif q == "numeric":
                idx = NumericIndex(
                    store_path=self._store.store_path,
                    service=self._store.config.name,
                    field=field_name,
                    ram_threshold_bytes=threshold,
                )
                self._numeric_indexes[field_name] = idx
                if not os.path.exists(idx.path):
                    needs_rebuild.append(field_name)

        if needs_rebuild:
            store_name = self._store.config.name
            print(f"[{store_name}] Index files missing for: {needs_rebuild} — rebuilding from stored data...")
            for field_name in needs_rebuild:
                try:
                    self.rebuild_index(field_name)
                except Exception as exc:
                    import traceback
                    print(f"[{store_name}] ERROR: rebuild_index('{field_name}') failed: {exc}")
                    traceback.print_exc()
                    logger.warning("build_indexes: rebuild_index('%s') failed: %s", field_name, exc)
        else:
            print(f"[{self._store.config.name}] All index files present — loaded from disk.")

    def on_write(self, record_id: str, flat_delta: dict) -> None:
        """Update all relevant indexes when a record is written."""
        for field, idx in self._full_text_indexes.items():
            if field in flat_delta:
                idx.add(record_id, str(flat_delta[field]))
        for field, idx in self._numeric_indexes.items():
            if field in flat_delta:
                try:
                    idx.add(record_id, flat_delta[field])
                except (ValueError, TypeError):
                    pass

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
        for record_id in store.index_manager.all_record_ids():
            entry = store.index_manager.get(record_id)
            if entry is None:
                continue
            seg, offset, size = entry
            try:
                line = store.log_manager.read(seg, offset, size)
                _, flat = _ser.deserialize_record(line)
                if field_name in flat:
                    new_idx.add(record_id, flat[field_name])
            except Exception as exc:
                logger.warning("rebuild_index: skipping %s: %s", record_id, exc)

        new_idx.save()
        n = len(new_idx._map)
        print(f"[{store.config.name}] Index for '{field_name}' rebuilt: {n} entries — saved to {new_idx.path}")
        logger.info("rebuild_index: field '%s' rebuilt (%d entries)", field_name, n)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(self, filter_dict: dict) -> list:
        """
        Execute a declarative query dict and return a sorted list of matching record IDs.
        Raises QuerySyntaxError for malformed input.
        """
        if not isinstance(filter_dict, dict):
            raise QuerySyntaxError("filter_dict must be a dict")

        # Empty filter → return all record IDs
        if not filter_dict:
            idx_mgr = self._store.index_manager
            if idx_mgr is None:
                return []
            return sorted(idx_mgr.all_record_ids())

        # ID fast path
        if "id" in filter_dict:
            ids = filter_dict["id"]
            if isinstance(ids, str):
                return [ids]
            if isinstance(ids, list):
                return list(ids)
            raise QuerySyntaxError("'id' value must be a string or list of strings")

        candidate_sets: list = []

        for field, value in filter_dict.items():
            candidates = self._eval_field(field, value)
            if not candidates:          # short-circuit
                return []
            candidate_sets.append(candidates)

        if not candidate_sets:
            return []

        result = candidate_sets[0]
        for s in candidate_sets[1:]:
            result = result & s
        return sorted(result)

    def _eval_field(self, field: str, value) -> set:
        """Evaluate one field clause and return a set of matching record IDs."""
        if isinstance(value, (int, float)):
            # Exact numeric match
            idx = self._numeric_indexes.get(field)
            if idx is None:
                raise QuerySyntaxError(
                    f"Field '{field}' has no numeric index. "
                    "Mark it queryable='numeric' in schema_config."
                )
            return idx.exact_query(value)

        if isinstance(value, str):
            # Exact text match — linear scan
            result = set()
            idx_mgr = self._store.index_manager
            if idx_mgr is None:
                return result
            for record_id in idx_mgr.all_record_ids():
                rec = self._store.read(record_id)
                if rec and rec.get(field) == value:
                    result.add(record_id)
            return result

        if isinstance(value, list):
            # Partial / tokenised full-text search (AND across keywords)
            idx = self._full_text_indexes.get(field)
            if idx is None:
                raise QuerySyntaxError(
                    f"Field '{field}' has no full-text index. "
                    "Mark it queryable='full-text' in schema_config."
                )
            return idx.search(value, mode="and")

        if isinstance(value, dict):
            # Numeric range query — keys are operators, values are numbers
            idx = self._numeric_indexes.get(field)
            if idx is None:
                raise QuerySyntaxError(
                    f"Field '{field}' has no numeric index. "
                    "Mark it queryable='numeric' in schema_config."
                )
            lo = hi = None
            lo_inc = hi_inc = True
            for op, bound in value.items():
                op = _OP_ALIASES.get(op, op)   # normalise gte/lte/gt/lt
                if op not in OPERATORS:
                    raise QuerySyntaxError(
                        f"Unsupported operator '{op}'. Must be one of {sorted(OPERATORS)}."
                    )
                if op == ">=":
                    lo, lo_inc = bound, True
                elif op == ">":
                    lo, lo_inc = bound, False
                elif op == "<=":
                    hi, hi_inc = bound, True
                elif op == "<":
                    hi, hi_inc = bound, False
            return idx.range_query(lo=lo, hi=hi, lo_inclusive=lo_inc, hi_inclusive=hi_inc)

        raise QuerySyntaxError(
            f"Unsupported value type {type(value).__name__!r} for field '{field}'. "
            "Use int/float (exact numeric), str (exact text), "
            "list (keyword search), or dict (range query)."
        )
