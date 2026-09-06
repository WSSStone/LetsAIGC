from copy import deepcopy

from test_ui_inpaint_object_info import EXPECTED_MODULES, _validate_native_node_contracts


def _nodes():
    result = {name: {"python_module": module} for name, module in EXPECTED_MODULES.items()}
    result["ImageToMask"]["input"] = {
        "required": {"channel": ["COMBO", {"multiselect": False, "options": ["red", "green", "blue", "alpha"]}]}
    }
    result["VAEEncodeForInpaint"]["input"] = {
        "required": {"grow_mask_by": ["INT", {"default": 6, "min": 0, "max": 64, "step": 1}]}
    }
    return result


def test_native_mask_node_is_accepted_with_explicit_zero_grow():
    result = _validate_native_node_contracts(_nodes())
    assert not result["wrong_python_modules"]
    assert not result["external_python_modules"]
    assert result["image_to_mask_channel"]["red_supported"]
    assert result["vae_encode_for_inpaint_grow_mask_by"]["supports_zero"]


def test_unrelated_external_node_cannot_hide_behind_native_prefix():
    nodes = _nodes()
    nodes["ForeignNode"] = {"python_module": "nodes_external_plugin"}
    result = _validate_native_node_contracts(nodes)
    assert result["external_python_modules"] == ["nodes_external_plugin"]


def test_changed_mask_channel_and_minimum_are_visible_failures():
    nodes = deepcopy(_nodes())
    nodes["ImageToMask"]["input"]["required"]["channel"][1]["options"] = ["alpha"]
    nodes["VAEEncodeForInpaint"]["input"]["required"]["grow_mask_by"][1]["min"] = 1
    result = _validate_native_node_contracts(nodes)
    assert not result["image_to_mask_channel"]["red_supported"]
    assert not result["vae_encode_for_inpaint_grow_mask_by"]["supports_zero"]


def test_service_proof_cannot_combine_two_different_processes():
    from test_ui_inpaint_object_info import _service_matches_lock

    lock = {"listen": "127.0.0.1", "port": 8188}
    correct = {
        "pid": 100,
        "executable_matches_environment": True,
        "environment_matches_lock": True,
        "listen": "127.0.0.1",
        "port": 8188,
    }
    wrong = {**correct, "pid": 200, "executable_matches_environment": False}
    service = {
        "candidates": [correct, wrong],
        "listeners": [200],
        "listener_matches_candidate": True,
    }
    assert not _service_matches_lock(service, lock)
    service["listeners"] = [100]
    assert _service_matches_lock(service, lock)
