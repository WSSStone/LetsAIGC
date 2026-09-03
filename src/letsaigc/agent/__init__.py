from .storage import AgentStore

__all__ = ["AgentOrchestrator", "AgentStore"]


def __getattr__(name: str):
    # The workflow compiler imports approval utilities without importing the
    # orchestrator (which itself imports generation backends).
    if name == "AgentOrchestrator":
        from .orchestrator import AgentOrchestrator

        return AgentOrchestrator
    raise AttributeError(name)
