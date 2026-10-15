from .base import Store
from .memory import MemoryStore


def build_store(backend: str, project: str | None = None) -> Store:
    if backend == "firestore":
        from .firestore import FirestoreStore
        return FirestoreStore(project=project)
    return MemoryStore()


__all__ = ["Store", "MemoryStore", "build_store"]
