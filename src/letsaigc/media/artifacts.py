from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from ..errors import RuntimeExecutionError, ValidationError
from ..schemas import MediaOutputDeclaration


@dataclass(frozen=True)
class DiscoveredOutput:
    path: Path
    role: str
    media_kind: str


def _safe_output_path(output_root: Path, subfolder: str, filename: str) -> Path:
    # ComfyUI records can come from Windows even when this client runs on POSIX.
    parts = [Path(value.replace("\\", "/")) for value in (subfolder, filename)]
    if not filename or any(
        part.is_absolute()
        or PureWindowsPath(value).root
        or ".." in part.parts
        or PureWindowsPath(value).drive
        for value, part in zip((subfolder, filename), parts, strict=True)
    ):
        raise ValidationError("ComfyUI output must use a safe relative path")
    relative = parts[0] / parts[1]
    root = output_root.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ValidationError(f"ComfyUI output escaped the configured output root: {relative}")
    return candidate


def discover_declared_outputs(
    history: dict,
    declarations: list[MediaOutputDeclaration],
    output_root: Path,
) -> list[DiscoveredOutput]:
    discovered: list[DiscoveredOutput] = []
    outputs = history.get("outputs", {})
    for declaration in declarations:
        node = outputs.get(declaration.node_id, {})
        records = node.get(declaration.history_field, [])
        found = 0
        for record in records if isinstance(records, list) else []:
            if not isinstance(record, dict) or record.get("type", "output") != "output":
                continue
            filename = str(record.get("filename", ""))
            candidate = _safe_output_path(output_root, str(record.get("subfolder", "")), filename)
            if candidate.suffix.lower() not in declaration.allowed_extensions:
                raise ValidationError(f"Undeclared output extension for role {declaration.role}: {candidate.suffix}")
            if not candidate.is_file():
                raise RuntimeExecutionError(f"Declared ComfyUI output is missing: {candidate}")
            discovered.append(DiscoveredOutput(candidate, declaration.role, declaration.media_kind))
            found += 1
        if declaration.required and found == 0:
            raise RuntimeExecutionError(f"ComfyUI completed without required output role: {declaration.role}")
    return discovered
