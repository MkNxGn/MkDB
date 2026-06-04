"""
src.db.query_workers
====================
Query worker pool infrastructure.

Public surface
--------------
QueryDispatcher  — create one per store; call .start() then .submit(op, params)
QueryTask        — the picklable unit of work; mostly internal
OPERATIONS       — frozenset of valid operation strings
"""

from mkdb.db.query_workers.dispatcher import QueryDispatcher
from mkdb.db.query_workers.task import QueryTask, OPERATIONS

__all__ = ["QueryDispatcher", "QueryTask", "OPERATIONS"]
