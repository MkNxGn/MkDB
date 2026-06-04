"""
QueryTask — the unit of work placed on the shared work queue.

Instances must be fully picklable so they can be sent through a
multiprocessing.Queue to worker processes.
"""

import uuid
from dataclasses import dataclass, field


# Operations a worker can execute.
OPERATIONS = frozenset({"read", "query", "count", "exists", "multi_read"})


@dataclass
class QueryTask:
    """
    A single query request.

    Fields
    ------
    operation : str
        One of OPERATIONS.
    store_name : str
        Name of the target store.
    params : dict
        Operation-specific parameters:

        read / exists:
            {"record_id": str}

        multi_read:
            {"record_ids": list[str]}

        query:
            {"filter": dict}   — e.g. {"price": {"$gte": 10, "$lte": 50},
                                        "name": {"$text": "steel bolt"}}

        count:
            {"filter": dict}   — same filter format; returns an integer

    task_id : str
        Auto-generated UUID hex string. Used by the dispatcher to route
        results back to the correct waiter.
    """

    operation: str
    store_name: str
    params: dict
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self):
        if self.operation not in OPERATIONS:
            raise ValueError(
                f"Unknown operation {self.operation!r}. Must be one of {sorted(OPERATIONS)}"
            )
        if not self.store_name:
            raise ValueError("store_name must not be empty")
        if not isinstance(self.params, dict):
            raise TypeError("params must be a dict")

    def to_dict(self) -> dict:
        return {
            "task_id":    self.task_id,
            "operation":  self.operation,
            "store_name": self.store_name,
            "params":     self.params,
        }

    @staticmethod
    def from_dict(d: dict) -> "QueryTask":
        return QueryTask(
            operation=d["operation"],
            store_name=d["store_name"],
            params=d["params"],
            task_id=d.get("task_id", uuid.uuid4().hex),
        )
