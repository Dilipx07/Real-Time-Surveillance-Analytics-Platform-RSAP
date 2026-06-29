from fastapi.testclient import TestClient

from app.dependencies import get_db
from main import app


def test_all_protected_http_operations_expose_dual_token_contract():
    schema = app.openapi()
    exemptions = {
        ("/api/v1/auth/login", "post"),
        ("/api/v1/licenses/{license_id}/verify", "get"),
        ("/health", "get"),
    }
    for path, path_item in schema["paths"].items():
        if not path.startswith("/api/v1/") and path != "/health":
            continue
        for method, operation in path_item.items():
            if method not in {"get", "post", "patch", "delete", "put"} or (path, method) in exemptions:
                continue
            assert operation.get("security"), f"{method.upper()} {path} has no bearer-token security"
            header_names = {
                parameter["name"] for parameter in operation.get("parameters", []) if parameter.get("in") == "header"
            }
            assert "X-Session-Token" in header_names, f"{method.upper()} {path} has no session-token header"


def test_validation_errors_use_response_envelope():
    async def unused_db():
        yield None

    app.dependency_overrides[get_db] = unused_db
    client = TestClient(app)
    try:
        response = client.post("/api/v1/auth/login", json={})
        assert response.status_code == 422
        assert response.json()["success"] is False
        assert response.json()["data"] is None
        assert response.json()["error"]
    finally:
        app.dependency_overrides.clear()


def test_openapi_contains_required_api_groups():
    paths = app.openapi()["paths"]
    expected = {
        "/api/v1/auth/login", "/api/v1/users/", "/api/v1/licenses/", "/api/v1/cameras/",
        "/api/v1/persons/", "/api/v1/analytics/events", "/api/v1/sync/events",
    }
    assert expected.issubset(paths)
