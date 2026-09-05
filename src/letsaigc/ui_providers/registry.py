"""No dynamic imports from user/provider supplied names."""

from ..pipelines.errors import PipelineError


def resolve_provider(provider_id: str, providers: dict):
    if provider_id not in {"manual", "search"}:
        raise PipelineError("prohibited_capability")
    provider = providers.get(provider_id)
    if provider is None:
        raise PipelineError("capability_not_ready", "The requested UI provider is not configured")
    if provider.id != provider_id:
        raise PipelineError("prohibited_capability")
    return provider
