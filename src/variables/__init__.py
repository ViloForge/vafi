"""vafi variables substrate runtime (C.3)."""
from .backend import SecretBackend
from .literal import LiteralBackend
from .registry import BackendRegistry
from .types import FetchResult, Role, VarName, VarRef

__all__ = [
    "FetchResult",
    "Role",
    "VarName",
    "VarRef",
    "SecretBackend",
    "LiteralBackend",
    "BackendRegistry",
]
