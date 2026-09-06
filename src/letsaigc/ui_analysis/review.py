"""Deterministic human corrections. No model, network or approval authority."""

import math
from uuid import NAMESPACE_URL, uuid5

from ..pipelines.errors import PipelineError
from ..schemas.ui_review import ReviewDocument


def adapt_layout(task_id, layout, texts):
    if layout.get("schema_version") != 1:
        raise PipelineError("review_schema_unsupported")
    elements = []
    for item in layout["elements"]:
        kind = item["kind"]
        base = {
            "text": "text",
            "image": "image",
            "icon": "image",
            "map": "image",
            "button": "container",
            "panel": "container",
            "bar": "container",
            "other": "other",
        }.get(kind)
        if base is None:
            raise PipelineError("review_schema_unsupported")
        elements.append(
            {
                "element_id": item["element_id"],
                "base_type": base,
                "semantic_tags": [kind] if kind in {"icon", "map", "button", "panel", "bar"} else [],
                "bbox": integer_box(item["bbox"]),
                "parent_id": item.get("parent_id"),
                "text_region_ids": item.get("text_ids", []),
                "field_sources": {name: "model" for name in ("bbox", "base_type", "parent_id")},
            }
        )
    words = []
    for item in texts["texts"]:
        polygon = item["polygon"]
        words.append(
            {
                "text_region_id": item["text_id"],
                "ocr_text_id": item["text_id"],
                "effective_text": item["text"],
                "original_text": item["text"],
                "original_score": item.get("score"),
                "original_polygon": polygon,
                "bbox": integer_box(
                    [
                        min(p[0] for p in polygon),
                        min(p[1] for p in polygon),
                        max(p[0] for p in polygon),
                        max(p[1] for p in polygon),
                    ]
                ),
                "origin": "ocr",
                "geometry_origin": "ocr",
            }
        )
    return ReviewDocument(
        task_id=task_id,
        source_id=layout["source_id"],
        width=layout["width"],
        height=layout["height"],
        elements=elements,
        texts=words,
    )


def integer_box(box):
    return [math.floor(box[0]), math.floor(box[1]), math.ceil(box[2]), math.ceil(box[3])]


def apply_actions(document, patch, *, restore=None):
    data = document.model_dump(mode="json")
    elements = {x["element_id"]: x for x in data["elements"]}
    words = {x["text_region_id"]: x for x in data["texts"]}
    mapping = {}
    original_ids = set(elements)
    for index, action in enumerate(patch.actions):
        kind = action.action
        if kind == "restore_revision":
            if len(patch.actions) != 1 or restore is None:
                raise PipelineError("review_invalid_restore")
            restored = restore(action.revision)
            if (restored.task_id, restored.source_id, restored.width, restored.height) != (
                document.task_id,
                document.source_id,
                document.width,
                document.height,
            ):
                raise PipelineError("review_input_unavailable")
            return restored, {}
        identity = mapping.get(action.element_id, action.element_id)
        if kind == "add_region":
            if action.element_id in elements or action.element_id in mapping:
                raise PipelineError("review_duplicate_identity")
            identity = "element-" + uuid5(NAMESPACE_URL, f"{document.task_id}/{patch.request_id}/{index}").hex
            if not set(action.replaces_ids or []) <= original_ids:
                raise PipelineError("review_invalid_reference")
            mapping[action.element_id] = identity
            text_ids = []
            if action.base_type == "text":
                text_id = "human-" + identity.removeprefix("element-")
                mapping[action.element_id + "-text"] = text_id
                words[text_id] = {
                    "text_region_id": text_id,
                    "effective_text": action.text or "",
                    "bbox": action.bbox,
                    "origin": "human",
                }
                text_ids.append(text_id)
            elements[identity] = {
                "element_id": identity,
                "base_type": action.base_type,
                "semantic_tags": action.semantic_tags or [],
                "bbox": action.bbox,
                "parent_id": None,
                "text_region_ids": text_ids,
                "locked_fields": [],
                "replaces_ids": action.replaces_ids or [],
                "field_sources": {k: "human" for k in ("bbox", "base_type", "text_region_ids")},
            }
            continue
        if kind == "set_text":
            text_region_id = mapping.get(action.text_region_id, action.text_region_id)
            if text_region_id not in words:
                raise PipelineError("review_invalid_reference")
            if any("text" in e["locked_fields"] for e in elements.values() if text_region_id in e["text_region_ids"]):
                raise PipelineError("review_locked")
            words[text_region_id].update(effective_text=action.text, origin="human")
            continue
        if identity not in elements:
            raise PipelineError("review_invalid_reference")
        item = elements[identity]
        field = {
            "update_box": "bbox",
            "set_type": "base_type",
            "set_tags": "semantic_tags",
            "set_parent": "parent_id",
            "set_text_links": "text_region_ids",
        }.get(kind)
        if kind != "set_lock" and (
            field in item["locked_fields"] or (kind == "delete_region" and item["locked_fields"])
        ):
            raise PipelineError("review_locked")
        if field:
            value = getattr(action, field)
            if field == "parent_id":
                value = mapping.get(value, value)
            if field == "text_region_ids":
                value = [mapping.get(identity, identity) for identity in value]
            item[field] = value
            item["field_sources"][field] = "human"
            if field == "bbox" and item["base_type"] == "text" and len(item["text_region_ids"]) == 1:
                word = words[item["text_region_ids"][0]]
                word.update(bbox=value, geometry_origin="human")
        elif kind == "set_lock":
            item["locked_fields"] = list(dict.fromkeys(action.fields))
        elif kind == "delete_region":
            children = [e for e in elements.values() if e["parent_id"] == identity]
            if children and action.children is None:
                raise PipelineError("review_children_policy_required")
            removing = {identity}
            if action.children == "delete_subtree":
                while True:
                    added = {e["element_id"] for e in elements.values() if e["parent_id"] in removing} - removing
                    if not added:
                        break
                    removing |= added
                if any(elements[x]["locked_fields"] for x in removing):
                    raise PipelineError("review_locked")
            else:
                for child in children:
                    if "parent_id" in child["locked_fields"]:
                        raise PipelineError("review_locked")
                    child["parent_id"] = None
                    child["field_sources"]["parent_id"] = "human"
            elements = {k: v for k, v in elements.items() if k not in removing}
    data.update(elements=list(elements.values()), texts=list(words.values()))
    return ReviewDocument.model_validate(data), mapping


def encoded(value):
    import json

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ReviewRepository:
    """Local trusted domain API. HTTP authentication belongs to review_server."""

    def __init__(self, ledger, store, *, resources=None):
        self.ledger, self.store, self.resources = ledger, store, resources
        with ledger.transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in {4, 5}:
                raise PipelineError("migration_required")

    def put(self, task_id, operation, value, role, media_type="application/json"):
        from .resources import check_storage

        data = value if isinstance(value, bytes) else encoded(value).encode()
        if self.resources is not None:
            try:
                check_storage(self.store, task_id, self.resources, extra_bytes=len(data))
            except PipelineError as exc:
                if exc.code == "resource_insufficient":
                    raise PipelineError("review_storage_insufficient") from exc
                raise
        try:
            return self.store.put(task_id, operation, data, role=role, media_type=media_type)
        except OSError as exc:
            raise PipelineError("review_storage_insufficient") from exc

    def initialize(self, document, base_refs):
        import json

        with self.ledger.transaction() as db:
            old = db.execute("SELECT base_refs FROM ui_review_heads WHERE task_id=?", (document.task_id,)).fetchone()
            if old:
                if json.loads(old["base_refs"]) != base_refs:
                    raise PipelineError("review_input_changed")
                return
            ref = self.put(document.task_id, "review-baseline", document, "review_draft")
            db.execute(
                "INSERT INTO ui_review_heads(task_id,base_refs) VALUES(?,?)", (document.task_id, encoded(base_refs))
            )
            db.execute(
                "INSERT INTO ui_review_revisions(task_id,revision,draft_ref) VALUES(?,0,?)",
                (document.task_id, encoded(ref)),
            )

    def head(self, task_id):
        import json

        with self.ledger.transaction() as db:
            row = db.execute("SELECT * FROM ui_review_heads WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise PipelineError("review_input_unavailable")
            result = dict(row)
            result["base_refs"] = json.loads(result["base_refs"])
            result["history"] = [
                dict(r)
                for r in db.execute(
                    "SELECT revision,published_refs IS NOT NULL AS confirmed FROM ui_review_revisions "
                    "WHERE task_id=? ORDER BY revision DESC",
                    (task_id,),
                )
            ]
            return result

    def _document(self, db, task_id, revision):
        from ..schemas.pipeline import ArtifactRef

        row = db.execute(
            "SELECT draft_ref FROM ui_review_revisions WHERE task_id=? AND revision=?", (task_id, revision)
        ).fetchone()
        if row is None:
            raise PipelineError("review_input_unavailable")
        ref = ArtifactRef.model_validate_json(row["draft_ref"])
        if ref.task_id != task_id:
            raise PipelineError("artifact_scope")
        return ReviewDocument.model_validate_json(self.store.read(ref))

    def document(self, task_id, revision):
        with self.ledger.transaction() as db:
            return self._document(db, task_id, revision)

    def request(self, task_id, request_id):
        import json

        with self.ledger.transaction() as db:
            row = db.execute(
                "SELECT result FROM ui_review_requests WHERE task_id=? AND request_id=?", (task_id, request_id)
            ).fetchone()
        if row is None:
            raise PipelineError("review_request_unavailable")
        return json.loads(row["result"])

    def _existing(self, db, task_id, request, kind):
        import hashlib
        import json

        payload_hash = hashlib.sha256(encoded(request).encode()).hexdigest()
        old = db.execute(
            "SELECT * FROM ui_review_requests WHERE task_id=? AND request_id=?", (task_id, request.request_id)
        ).fetchone()
        if old and (old["payload_hash"] != payload_hash or old["kind"] != kind):
            raise PipelineError("review_request_conflict")
        return payload_hash, json.loads(old["result"]) if old else None

    def _record(self, db, task_id, request, kind, payload_hash, result):
        db.execute(
            "INSERT INTO ui_review_requests VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(task_id,request_id) DO UPDATE SET state=excluded.state,result=excluded.result",
            (task_id, request.request_id, payload_hash, kind, result["state"], encoded(result)),
        )

    def save(self, task_id, patch):
        try:
            return self._save(task_id, patch)
        except PipelineError as exc:
            if exc.code == "review_storage_insufficient":
                with self.ledger.transaction() as db:
                    payload_hash, _ = self._existing(db, task_id, patch, "save")
                    self._record(
                        db,
                        task_id,
                        patch,
                        "save",
                        payload_hash,
                        {"request_id": patch.request_id, "state": "failed", "error": exc.code},
                    )
            raise

    def _save(self, task_id, patch):
        from ..schemas.ui_review import ReviewPatch

        patch = ReviewPatch.model_validate(patch.model_dump(exclude_unset=True))
        if len(encoded(patch).encode()) > 1024**2:
            raise PipelineError("review_payload_too_large")
        with self.ledger.transaction() as db:
            payload_hash, old = self._existing(db, task_id, patch, "save")
            if old and old["state"] != "failed":
                result = old
            else:
                head = db.execute("SELECT * FROM ui_review_heads WHERE task_id=?", (task_id,)).fetchone()
                if head is None:
                    raise PipelineError("review_input_unavailable")
                result = {"request_id": patch.request_id, "state": "conflict", "error": "review_conflict"}
                if (head["draft_revision"], head["confirmed_revision"]) == (
                    patch.base_draft_revision,
                    patch.base_confirmed_revision,
                ):
                    document, mapping = apply_actions(
                        self._document(db, task_id, head["draft_revision"]),
                        patch,
                        restore=lambda rev: self._document(db, task_id, rev),
                    )
                    revision = head["draft_revision"] + 1
                    operation = "review-" + payload_hash
                    patch_ref = self.put(task_id, operation, patch.model_dump(exclude_unset=True), "review_patch")
                    draft_ref = self.put(task_id, operation, document, "review_draft")
                    db.execute(
                        "INSERT INTO ui_review_revisions(task_id,revision,draft_ref,patch_ref) VALUES(?,?,?,?)",
                        (task_id, revision, encoded(draft_ref), encoded(patch_ref)),
                    )
                    db.execute("UPDATE ui_review_heads SET draft_revision=? WHERE task_id=?", (revision, task_id))
                    result = {"request_id": patch.request_id, "state": "saved", "revision": revision, "id_map": mapping}
                self._record(db, task_id, patch, "save", payload_hash, result)
        if result["state"] == "conflict":
            raise PipelineError("review_conflict")
        return result

    def confirm(self, task_id, request):
        from .review_projection import confirmation_lock

        with confirmation_lock(self.store, task_id):
            return self._confirm(task_id, request)

    def _confirm(self, task_id, request):
        from .review_projection import materialize

        with self.ledger.transaction() as db:
            payload_hash, old = self._existing(db, task_id, request, "confirm")
            if old and old["state"] in {"confirmed", "conflict"}:
                if old["state"] == "conflict":
                    raise PipelineError("review_conflict")
                return old
            row = db.execute("SELECT * FROM ui_review_heads WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise PipelineError("review_input_unavailable")
            matches = (row["draft_revision"], row["confirmed_revision"]) == (
                request.draft_revision,
                request.expected_confirmed_revision,
            )
            result = {
                "request_id": request.request_id,
                "state": "confirming" if matches else "conflict",
                "revision": request.draft_revision,
                "confirmation": request.model_dump(mode="json"),
            }
            published = db.execute(
                "SELECT published_refs FROM ui_review_revisions WHERE task_id=? AND revision=?",
                (task_id, request.draft_revision),
            ).fetchone()
            if matches and published and published["published_refs"]:
                import json

                result.update(state="confirmed", manifest_ref=json.loads(published["published_refs"])["manifest_ref"])
            self._record(db, task_id, request, "confirm", payload_hash, result)
        if not matches:
            raise PipelineError("review_conflict")
        if result["state"] == "confirmed":
            return result
        try:
            refs = materialize(self, task_id, request)
            with self.ledger.transaction() as db:
                changed = db.execute(
                    "UPDATE ui_review_heads SET confirmed_revision=? WHERE task_id=? "
                    "AND draft_revision=? AND confirmed_revision IS ?",
                    (request.draft_revision, task_id, request.draft_revision, request.expected_confirmed_revision),
                ).rowcount
                if changed:
                    db.execute(
                        "UPDATE ui_review_revisions SET published_refs=? WHERE task_id=? AND revision=?",
                        (encoded(refs), task_id, request.draft_revision),
                    )
                    result.update(state="confirmed", manifest_ref=refs["manifest_ref"])
                else:
                    result.update(state="conflict", error="review_conflict")
                self._record(db, task_id, request, "confirm", payload_hash, result)
        except Exception as exc:
            code = exc.code if isinstance(exc, PipelineError) else "review_materialization_failed"
            result.update(state="failed", error=code)
            with self.ledger.transaction() as db:
                self._record(db, task_id, request, "confirm", payload_hash, result)
            raise PipelineError(code) from exc
        if result["state"] == "conflict":
            raise PipelineError("review_conflict")
        return result

    def pending_confirmation(self, task_id):
        import json

        from ..schemas.ui_review import ReviewConfirm

        with self.ledger.transaction() as db:
            row = db.execute(
                "SELECT result FROM ui_review_requests WHERE task_id=? AND state='confirming' ORDER BY rowid LIMIT 1",
                (task_id,),
            ).fetchone()
        return ReviewConfirm.model_validate(json.loads(row["result"])["confirmation"]) if row else None

    def binding(self, task_id, revision):
        import json

        from ..schemas.pipeline import ArtifactRef
        from ..schemas.ui_review import ReviewedLayoutBinding

        with self.ledger.transaction() as db:
            row = db.execute(
                "SELECT published_refs FROM ui_review_revisions WHERE task_id=? AND revision=?", (task_id, revision)
            ).fetchone()
        if not row or not row["published_refs"]:
            raise PipelineError("review_not_confirmed")
        refs = json.loads(row["published_refs"])
        manifest_ref = ArtifactRef.model_validate(refs["manifest_ref"])
        manifest = json.loads(self.store.read(manifest_ref))
        if manifest["task_id"] != task_id or manifest["review_revision"] != revision:
            raise PipelineError("artifact_scope")
        if manifest["outputs"] != {k: v for k, v in refs.items() if k != "manifest_ref"}:
            raise PipelineError("artifact_changed")
        binding = ReviewedLayoutBinding(
            task_id=task_id,
            review_revision=revision,
            review_manifest_ref=manifest_ref,
            layout_ref=refs["layout_ref"],
            texts_ref=refs["texts_ref"],
            canonical_ref=manifest["base_refs"]["canonical_ref"],
        )
        for ref in (binding.layout_ref, binding.texts_ref, binding.canonical_ref):
            self.store.read(ref)
        return binding


def open_review(service, task_id):
    """Only immutable, succeeded parse inputs may enter the local correction flow."""
    import json

    from ..execution.temporal.projection import local_projection
    from ..schemas.pipeline import ArtifactRef
    from .execution import UIExecution

    try:
        plan = service.ledger.plan(task_id)
        run = local_projection(service, task_id)
        if not run or run["state"] != "succeeded":
            raise PipelineError("review_input_unavailable")
        execution = UIExecution(service)
        request = execution.request(plan)
        if request.output_mode != "parse":
            raise PipelineError("review_input_unavailable")
        canonical, _ = execution.canonical(plan)
        outputs = execution.outputs(plan)
        refs = {role: next(r for r in outputs if r.role == role) for role in ("layout", "texts", "manifest")}
        refs["canonical"] = canonical.canonical_ref
        for ref in refs.values():
            if ref.task_id != task_id:
                raise PipelineError("artifact_scope")
            service.artifacts.read(ref)
        manifest = json.loads(service.artifacts.read(refs["manifest"]))
        if manifest["task_id"] != task_id or manifest["plan_fingerprint"] != plan.fingerprint:
            raise PipelineError("review_input_unavailable")
        declared = {ArtifactRef.model_validate(r).sha256 for r in manifest["outputs"]}
        if not {refs["layout"].sha256, refs["texts"].sha256} <= declared:
            raise PipelineError("review_input_unavailable")
        layout = json.loads(service.artifacts.read(refs["layout"]))
        if layout["canonical_sha256"] != canonical.canonical_ref.sha256:
            raise PipelineError("review_input_unavailable")
        document = adapt_layout(task_id, layout, json.loads(service.artifacts.read(refs["texts"])))
        if (document.width, document.height, document.source_id) != (
            canonical.width,
            canonical.height,
            canonical.source_id,
        ):
            raise PipelineError("review_input_unavailable")
        base_refs = {
            "canonical_ref": refs["canonical"].model_dump(mode="json"),
            **{"base_" + k + "_ref": v.model_dump(mode="json") for k, v in refs.items() if k != "canonical"},
        }
        crop_index = next((r for r in outputs if r.role == "asset_index"), None)
        if crop_index:
            base_refs["base_crop_index_ref"] = crop_index.model_dump(mode="json")
        repo = ReviewRepository(service.ledger, service.artifacts, resources=request.resources)
        repo.initialize(document, base_refs)
        return repo
    except PipelineError as exc:
        if exc.code in {"migration_required", "review_schema_unsupported", "review_storage_insufficient"}:
            raise
        raise PipelineError("review_input_unavailable") from exc
    except (ValueError, KeyError, StopIteration, OSError) as exc:
        raise PipelineError("review_input_unavailable") from exc
