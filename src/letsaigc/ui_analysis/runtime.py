"""Local configuration and readiness; no model acquisition or paid probes."""

import shutil

import psutil
import yaml

from ..agent.responses import DEFAULT_VLM_MODEL, endpoint_fingerprint, load_llm_api_key
from ..agent.ui_analyzer import EVIDENCE_POLICY, UIAnalysisBackend, UIVLMPolicy
from ..config import get_setting
from ..generation.pricing import calculate_luna_cost, ensure_current, load_pricing
from ..paths import find_repo_root
from ..pipelines.contracts import Capability
from ..pipelines.errors import PipelineError
from ..schemas.pipeline import canonical_json, digest
from ..vision.client import OCROperationBackend, VisionClient
from ..vision.settings import load_ocr_settings
from .execution import UIExecution


def vision_settings():
    return load_ocr_settings()


def freeze_models(store, task_id):
    _, lock = vision_settings()
    pricing = load_pricing()
    price_ref = store.put(task_id, "plan", canonical_json(pricing).encode(), role="pricing")
    policy = UIVLMPolicy(
        model=get_setting("LLM_VLM_MODEL", DEFAULT_VLM_MODEL),
        endpoint_fingerprint=endpoint_fingerprint(),
        pricing_ref=price_ref,
        evidence_policy=EVIDENCE_POLICY,
    )
    return {
        "ocr": store.put(task_id, "plan", canonical_json(lock).encode(), role="model_lock"),
        "vlm": store.put(task_id, "plan", canonical_json(policy).encode(), role="vlm_policy"),
    }


def freeze_search(store, task_id, query):
    from ..assets.search import RoutingPolicy, SearchCriteria, SearchPricing
    from ..schemas.ui_provider import SearchUIInput

    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 1024:
        raise PipelineError("invalid_query")
    config = yaml.safe_load((find_repo_root() / "configs/providers/image-search.yaml").read_text(encoding="utf-8"))
    routing = RoutingPolicy.model_validate(config["routing"])
    criteria = SearchCriteria.model_validate(config["criteria"])
    prices = {
        name: store.put(
            task_id,
            "plan",
            canonical_json(SearchPricing.model_validate(config["pricing"][name])).encode(),
            role="pricing",
        )
        for name in routing.allowed_providers
    }
    return SearchUIInput(
        query_ref=store.put(task_id, "plan", canonical_json({"queries": [query.strip()]}).encode(), role="query"),
        criteria_ref=store.put(task_id, "plan", canonical_json(criteria).encode(), role="criteria"),
        routing_policy_ref=store.put(
            task_id,
            "plan",
            canonical_json(
                {
                    "routing": routing.model_dump(mode="json"),
                    "prices": {name: ref.model_dump(mode="json") for name, ref in prices.items()},
                }
            ).encode(),
            role="routing_policy",
        ),
    )


def vision_client():
    config, _ = vision_settings()
    return VisionClient(token=get_setting(config["token_env"], ""), endpoint=config["endpoint"])


class ConfiguredOCRBackend:
    capability = Capability(id="ui.ocr", can_cancel=True)

    def __init__(self, service):
        self.service = service

    def backend(self):
        config, lock = vision_settings()
        return OCROperationBackend(
            self.service.ledger,
            self.service.artifacts,
            vision_client(),
            signing_key=get_setting(config["signing_key_env"], ""),
            model_digest=digest(lock),
        )

    def submit(self, key, arguments):
        return self.call("submit", key, arguments)

    def recover(self, key):
        return self.call("recover", key)

    def inspect(self, receipt):
        return self.call("inspect", receipt)

    def collect(self, receipt):
        return self.call("collect", receipt)

    def cancel(self, receipt):
        return self.call("cancel", receipt)

    def call(self, method, *args):
        backend = self.backend()
        try:
            return getattr(backend, method)(*args)
        finally:
            backend.client.client.close()


def configure(service):
    UIExecution(service)
    service.backends.setdefault("ui.ocr", ConfiguredOCRBackend(service))
    service.backends.setdefault("ui.analyze", UIAnalysisBackend(service.ledger, service.artifacts))


def diagnose(*, include_search=True):
    from ..doctor import review_readiness
    from ..execution.temporal.config import diagnose as temporal_diagnose
    from ..vision.ocr import validate_model_files

    config, lock = vision_settings()
    ocr_files_ready = True
    try:
        validate_model_files(lock, find_repo_root())
    except PipelineError:
        ocr_files_ready = False
    try:
        package_lock_ready = all(
            value.get("version") and value.get("wheel_sha256") for value in lock["ocr"]["packages"].values()
        )
    except (AttributeError, KeyError, TypeError):
        package_lock_ready = False
    ocr_lock_ready = ocr_files_ready and package_lock_ready
    health = None
    try:
        client = vision_client()
        try:
            health = client.health()
        finally:
            client.client.close()
    except PipelineError:
        pass
    service_ready = bool(health and health.get("ready"))
    model_matches = bool(health and health.get("model_digest") == digest(lock))
    signing_ready = len(get_setting(config["signing_key_env"], "")) >= 32
    token_ready = bool(get_setting(config["token_env"], ""))
    ocr_reasons = []
    if not ocr_lock_ready:
        ocr_reasons.append("model_lock_unverified")
    if not service_ready:
        ocr_reasons.append("service_unreachable")
    elif not model_matches:
        ocr_reasons.append("service_model_mismatch")
    if not signing_ready or not token_ready:
        ocr_reasons.append("authentication_not_configured")
    ocr_ready = ocr_lock_ready and service_ready and model_matches and signing_ready and token_ready

    vlm_pricing_ready = False
    vlm_endpoint_ready = False
    vlm_credentials_ready = bool(load_llm_api_key())
    try:
        pricing = load_pricing()
        ensure_current(pricing)
        calculate_luna_cost(pricing, {}, model=get_setting("LLM_VLM_MODEL", DEFAULT_VLM_MODEL))
        vlm_pricing_ready = True
    except Exception:
        pass
    try:
        endpoint_fingerprint()
        vlm_endpoint_ready = True
    except Exception:
        pass
    vlm_reasons = []
    if not vlm_endpoint_ready:
        vlm_reasons.append("endpoint_invalid")
    if not vlm_credentials_ready:
        vlm_reasons.append("credentials_not_configured")
    if not vlm_pricing_ready:
        vlm_reasons.append("pricing_unverified")
    vlm_ready = vlm_endpoint_ready and vlm_credentials_ready and vlm_pricing_ready
    temporal = temporal_diagnose()
    temporal_reasons = []
    if not temporal.get("sdk_version"):
        temporal_reasons.append("sdk_not_installed")
    if not temporal.get("service_reachable"):
        temporal_reasons.append("service_unreachable")
    from ..assets.search import SearchPricing

    provider_status = {}
    if include_search:
        try:
            search = yaml.safe_load(
                (find_repo_root() / "configs/providers/image-search.yaml").read_text(encoding="utf-8")
            )
            provider_status = {}
            for name, value in search["pricing"].items():
                price = SearchPricing.model_validate(value)
                credentials_ready = bool(get_setting(name.upper() + "_API_KEY"))
                search_price_ready = price.ready("search")
                probe_price_ready = price.ready("probe")
                reasons = []
                if not credentials_ready:
                    reasons.append("credentials_not_configured")
                if not search_price_ready:
                    reasons.append("search_pricing_unverified")
                if not probe_price_ready:
                    reasons.append("probe_pricing_unverified")
                provider_status[name] = {
                    "ready": credentials_ready and search_price_ready and probe_price_ready,
                    "credentials_configured": credentials_ready,
                    "search_pricing_ready": search_price_ready,
                    "probe_pricing_ready": probe_price_ready,
                    "paid_probe_performed": False,
                    "reasons": reasons,
                }
        except Exception:
            provider_status = {
                name: {"ready": False, "reason": "invalid_configuration"} for name in ("serpapi", "tavily")
            }
    return {
        "manual": {"ready": True},
        "review": review_readiness(),
        "ocr": {
            "ready": ocr_ready,
            "protocol_version": 1,
            "model_digest": digest(lock),
            "model_lock_ready": ocr_lock_ready,
            "model_files_ready": ocr_files_ready,
            "package_lock_declared": package_lock_ready,
            "service_ready": service_ready,
            "service_model_matches": model_matches,
            "signing_configured": signing_ready,
            "token_configured": token_ready,
            "reasons": ocr_reasons,
        },
        "vlm": {
            "ready": vlm_ready,
            "endpoint_ready": vlm_endpoint_ready,
            "credentials_configured": vlm_credentials_ready,
            "pricing_ready": vlm_pricing_ready,
            "paid_probe_performed": False,
            "reasons": vlm_reasons,
        },
        "temporal": {"ready": temporal["status"] == "pass", "reasons": temporal_reasons},
        "search": {"ready": any(value["ready"] for value in provider_status.values()), "providers": provider_status},
        "segmentation": {"ready": False},
        "inpaint": {"ready": False},
        "batch": {"ready": False},
        "resources": {
            "free_disk_bytes": shutil.disk_usage(find_repo_root()).free,
            "available_ram_bytes": psutil.virtual_memory().available,
        },
    }


def preflight(service, plan):
    request = UIExecution(service).request(plan)
    configured = freeze_models(service.artifacts, plan.task_id)
    if configured != request.model_bindings:
        raise PipelineError("dependency_changed")
    status = diagnose(include_search=request.input.kind == "search")
    if not status["ocr"]["ready"] or not status["ocr"]["signing_configured"]:
        raise PipelineError("model_not_ready")
    if not status["vlm"]["ready"]:
        raise PipelineError("pricing_or_credentials_not_ready")
    if not status["temporal"]["ready"]:
        raise PipelineError("temporal_unavailable")
    if request.input.kind == "search":
        import json

        query = json.loads(service.artifacts.read(request.input.query_ref))["queries"][0]
        if freeze_search(service.artifacts, plan.task_id, query) != request.input:
            raise PipelineError("dependency_changed")
        if not status["search"]["ready"]:
            raise PipelineError("search_not_ready")
    if status["resources"]["free_disk_bytes"] < request.resources.minimum_free_disk_bytes:
        raise PipelineError("resource_insufficient")
