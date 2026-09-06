import pytest

from letsaigc.pipelines.errors import PipelineError


def test_session_is_single_use_origin_scoped_and_expiring():
    from letsaigc.ui_analysis.review_server import ReviewSession

    session = ReviewSession("127.0.0.1:12345", now=100)
    for host, origin in [("localhost:12345", "http://127.0.0.1:12345"), ("127.0.0.1:12345", "https://evil.example")]:
        with pytest.raises(PipelineError):
            session.exchange(session.bootstrap, host, origin, now=101)
    cookie, csrf = session.exchange(session.bootstrap, session.host, session.origin, now=101)
    with pytest.raises(PipelineError):
        session.exchange(session.bootstrap, session.host, session.origin, now=102)
    session.authorize(cookie, session.host, session.origin, csrf, write=True, now=102)
    for value in ("", "wrong"):
        with pytest.raises(PipelineError):
            session.authorize(cookie, session.host, session.origin, value, write=True, now=103)
    with pytest.raises(PipelineError):
        session.authorize(cookie, session.host, None, None, write=False, now=2000)
    expired = ReviewSession(session.host, now=0)
    with pytest.raises(PipelineError):
        expired.exchange(expired.bootstrap, expired.host, expired.origin, now=601)


def test_body_rejects_duplicate_keys_and_nonfinite_values():
    from letsaigc.ui_analysis.review_server import parse_body

    for body in (b'{"actions":[],"actions":[{}]}', b'{"x":NaN}', b"[]", b"x" * (1024**2 + 1)):
        with pytest.raises((PipelineError, ValueError)):
            parse_body(body)


def test_http_rejects_unauthenticated_cross_scope_and_hostile_requests(tmp_path):
    import threading

    import httpx

    from letsaigc.pipelines.service import PipelineService
    from letsaigc.schemas.ui_review import ReviewDocument
    from letsaigc.ui_analysis.review import ReviewRepository
    from letsaigc.ui_analysis.review_server import ReviewServer

    service = PipelineService(tmp_path, ui_schema=4)
    service.smoke_plan("http-review")
    repo = ReviewRepository(service.ledger, service.artifacts)
    repo.initialize(ReviewDocument(task_id="http-review", source_id="source", width=20, height=20), {})
    server = ReviewServer(repo, "http-review")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=server.session.origin, trust_env=False) as client:
            assert client.get("/api/review").status_code == 403
            assert client.get("/", headers={"Host": "evil.example"}).status_code == 403
            headers = {"Origin": server.session.origin}
            exchange = client.post("/api/session", json={"bootstrap": server.session.bootstrap}, headers=headers)
            assert exchange.status_code == 200
            assert "HttpOnly" in exchange.headers["set-cookie"] and "SameSite=Strict" in exchange.headers["set-cookie"]
            assert client.get("/api/review").status_code == 200
            patch = {"request_id": "write", "actions": []}
            assert client.post("/api/review/drafts", json=patch, headers=headers).status_code == 403
            headers["X-Review-CSRF"] = exchange.json()["csrf"]
            assert (
                client.post(
                    "/api/review/drafts", json=patch, headers={**headers, "Origin": "http://evil.example"}
                ).status_code
                == 403
            )
            for path in ("/api/approve", "/api/execute", "/api/search"):
                assert client.post(path, json={}, headers=headers).status_code == 404
            for path in ("/api/artifacts/foreign", "/api/artifacts/%2e%2e%2fsecret"):
                assert client.get(path).status_code == 403
            assert (
                client.post(
                    "/api/review/drafts",
                    content=b"x" * (1024**2 + 1),
                    headers={**headers, "Content-Type": "application/json"},
                ).status_code
                == 413
            )
            assert (
                client.post("/api/review/drafts", json={**patch, "approve": True}, headers=headers).status_code == 400
            )
            response = client.post("/api/review/drafts", json=patch, headers=headers)
            assert response.status_code == 200 and response.json()["revision"] == 1
            assert response.headers["Cache-Control"] == "no-store"
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
            assert "Access-Control-Allow-Origin" not in response.headers
            assert repo.head("http-review")["draft_revision"] == 1
            assert not service.ledger.list_operations("http-review")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
