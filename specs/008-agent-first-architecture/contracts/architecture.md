# Architecture Contract

Dependencies flow `CLI -> Agent -> deterministic tools -> backends -> evidence`.
The Development Harness may modify product files during development, but product
runtime code MUST NOT import, execute, or persist through Development Harness paths.

