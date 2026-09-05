"""Reference-only UI supply contracts; provider bodies live in the artifact store."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .pipeline import ArtifactRef, Identifier, PipelineModel


class ManualUIInput(PipelineModel):
    kind: Literal["manual"] = "manual"
    inputs: list[ArtifactRef] = Field(min_length=1, max_length=10)
    metadata_ref: ArtifactRef | None = None


class SearchUIInput(PipelineModel):
    kind: Literal["search"] = "search"
    query_ref: ArtifactRef
    criteria_ref: ArtifactRef
    routing_policy_ref: ArtifactRef
    max_images: int = Field(default=1, ge=1, le=10, strict=True)


UIInputSpec = Annotated[ManualUIInput | SearchUIInput, Field(discriminator="kind")]


class UISource(PipelineModel):
    source_id: Identifier
    original_ref: ArtifactRef
    provenance_ref: ArtifactRef
    input_entry_ids: list[Identifier] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def consistent_scope(self):
        if self.original_ref.task_id != self.provenance_ref.task_id or self.original_ref.role != "original":
            raise ValueError("Source references must share scope and preserve the original role")
        return self


class UIProvisionResult(PipelineModel):
    schema_version: Literal[1] = 1
    provider_id: Literal["manual", "search"]
    provider_version: str = Field(min_length=1, max_length=64)
    status: Literal["ready", "partial", "empty", "unavailable"]
    sources: list[UISource] = Field(default_factory=list, max_length=10)
    index_ref: ArtifactRef
    errors_ref: ArtifactRef | None = None

    @model_validator(mode="after")
    def status_matches_sources(self):
        if (self.status in {"ready", "partial"}) != bool(self.sources):
            raise ValueError("Supply status must describe actual sources")
        if self.status == "partial" and self.errors_ref is None:
            raise ValueError("Partial supply requires error evidence")
        refs = [self.index_ref, *([self.errors_ref] if self.errors_ref else [])]
        refs += [ref for source in self.sources for ref in (source.original_ref, source.provenance_ref)]
        if len({ref.task_id for ref in refs}) != 1:
            raise ValueError("Supply references must share task scope")
        return self


class UIInputEntry(PipelineModel):
    entry_id: Identifier
    status: Literal["ready", "failed"]
    source_id: Identifier | None = None
    duplicate_of: Identifier | None = None
    error_code: Identifier | None = None


class UIInputManifest(PipelineModel):
    schema_version: Literal[1] = 1
    task_id: Identifier
    status: Literal["ready", "partial", "unavailable"]
    entries: list[UIInputEntry] = Field(min_length=1, max_length=10)
    sources: list[UISource] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def complete_mapping(self):
        sources = {source.source_id for source in self.sources}
        if len(sources) != len(self.sources) or len({entry.entry_id for entry in self.entries}) != len(self.entries):
            raise ValueError("Input/source IDs must be unique")
        previous = set()
        for entry in self.entries:
            if (entry.status == "ready") != (entry.source_id in sources):
                raise ValueError("Entry requires its actual source or failure")
            if entry.duplicate_of and entry.duplicate_of not in previous:
                raise ValueError("Duplicate must reference an earlier input entry")
            previous.add(entry.entry_id)
        if any(source.original_ref.task_id != self.task_id for source in self.sources):
            raise ValueError("Input manifest references cross task scope")
        expected = (
            "unavailable"
            if not self.sources
            else "ready"
            if all(entry.status == "ready" for entry in self.entries)
            else "partial"
        )
        if expected != self.status:
            raise ValueError("Input status must reflect all entries")
        return self
