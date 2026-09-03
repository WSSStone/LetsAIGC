from ..errors import LetsAIGCError


class PipelineError(LetsAIGCError):
    """Public, stable errors never include provider payloads or credentials."""

    def __init__(self, code: str, message: str = "Pipeline operation could not complete") -> None:
        super().__init__(message)
        self.code = code
        self.details = {"code": code}


class OutcomeUnknown(PipelineError):
    def __init__(self) -> None:
        super().__init__("outcome_unknown", "External outcome requires reconciliation before further generation")
