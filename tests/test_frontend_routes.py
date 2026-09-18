"""Regression checks for the frontend application's deep-link contract."""

from fastapi.testclient import TestClient

from qisi_agent.api import create_app
from qisi_agent.auth import AuthStore


class _StaticIndex:
    chunks: list = []
    embedding_model = "fixture"


class _StaticRag:
    index = _StaticIndex()


def test_role_deep_links_return_the_frontend_application():
    app = create_app(_StaticRag(), auth=AuthStore())
    client = TestClient(app)

    for path in (
        "/",
        "/student/home",
        "/student/chat/example-session",
        "/teacher/overview",
        "/teacher/students/example-student",
        "/admin/login",
        "/admin/content",
        "/admin/quality/retrieval",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert 'id="app"' in response.text
        assert "/static/app.js" in response.text


def test_frontend_static_assets_are_available_without_external_font_services():
    app = create_app(_StaticRag(), auth=AuthStore())
    client = TestClient(app)

    index = client.get("/").text
    styles = client.get("/static/styles.css").text
    script = client.get("/static/app.js").text

    assert "fonts.googleapis" not in index
    assert "--ink:#17324d" in styles
    assert "function renderPractice" in script
    assert "const chunk = item.chunk || {}" in script
    assert "chunk.text || item.excerpt" in script
    assert "function roleForPath" in script
    assert "roleForPath() || state.authRole" in script
    assert "if (path === '/student/home') return renderHome();" in script
    assert "app.js?v=learning-path-5" in index
