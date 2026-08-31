class LetsAIGCError(RuntimeError):
    """Base error carrying a stable CLI exit category."""

    exit_code = 4

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class ValidationError(LetsAIGCError):
    exit_code = 2


class PolicyError(ValidationError):
    pass


class ReadinessError(LetsAIGCError):
    exit_code = 3


class RuntimeExecutionError(LetsAIGCError):
    exit_code = 4
