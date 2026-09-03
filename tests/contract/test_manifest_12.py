from letsaigc.schemas import AgentRunMetadata, LicenseLane, RunGovernance, RunManifest


def test_manifest_12_and_legacy_versions_are_readable() -> None:
    common = {
        "run_id": "run-1",
        "kind": "agent_task",
        "status": "created",
        "source": {},
        "parameters": {},
        "environment": {},
        "governance": RunGovernance(license_lanes=[LicenseLane.production], validations={}),
        "agent": AgentRunMetadata(session_id="session-1", task_id="task-1"),
    }
    assert RunManifest(**common).schema_version == "1.2"
    legacy = common | {"schema_version": "1.0", "kind": "inference", "agent": None}
    assert RunManifest(**legacy).schema_version == "1.0"
