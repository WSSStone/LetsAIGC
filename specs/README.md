# Feature specifications

The existing feature work is consolidated into `master`. Numbered directories and
`Feature ID` fields identify specifications, not active Git branches. Retain these
IDs for traceability; deleting a delivery branch does not delete its specification
or imply that pending acceptance tasks have passed.

Spec and plan templates record stable feature IDs rather than branch names.
Historical implementation dates and task evidence remain unchanged.

Speckit's normal feature-branch workflow remains available for future work. To
inspect an existing feature from `master`, use its existing process-local override
with the read-only prerequisite check (PowerShell, repository root):

```powershell
$previousFeature = $env:SPECIFY_FEATURE
try {
    $env:SPECIFY_FEATURE = '012-agent-critique-media'
    & ./.specify/scripts/powershell/check-prerequisites.ps1 -Json -PathsOnly
}
finally {
    $env:SPECIFY_FEATURE = $previousFeature
}
```

Select the intended feature directory explicitly before running any Speckit
operation that writes specifications. Do not recreate archived branches merely
to read their documents.
