from dataclasses import dataclass
from typing import Literal, Protocol

from ..assets.store import ArtifactStore
from ..pipelines.ledger import Ledger
from ..schemas.ui_provider import UIInputSpec, UIProvisionResult


@dataclass(frozen=True)
class ProvisionContext:
    task_id: str
    operation_id: str
    allowed_capabilities: tuple[str, ...]
    artifacts: ArtifactStore
    ledger: Ledger


class UIProvider(Protocol):
    id: Literal["manual", "search"]
    version: str

    def provide(self, request: UIInputSpec, context: ProvisionContext) -> UIProvisionResult: ...
