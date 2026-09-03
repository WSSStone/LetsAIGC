# Research: Constrained Comfy Workflow Compiler

- **Decision**: Recipes select trusted graph blocks; the Agent only fills schema-bounded
  fields. **Rationale**: Native-node allowlisting is reviewable and deterministic.
- **Decision**: Validate live `/object_info` before `/prompt` and upload resolved images
  with `/upload/image`. **Rationale**: Static recipe validity does not prove runtime compatibility.
- **Decision**: Compiled graphs are local-only; production still needs a committed recipe.

