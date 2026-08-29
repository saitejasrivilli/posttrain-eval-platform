def test_health_router_does_not_access_db_directly():
    import inspect

    from app.routers import health

    source = inspect.getsource(health)
    assert "text(" not in source
    assert "from sqlalchemy import" not in source
    assert "db.execute" not in source
    assert ".execute(" not in source


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_when_db_up(client):
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_readyz_when_db_down(client, monkeypatch):
    from app.routers import health

    def broken_execute(*args, **kwargs):
        raise Exception("db down")

    class FakeSession:
        def execute(self, *args, **kwargs):
            return broken_execute()

    def override():
        yield FakeSession()

    from app.db import get_db
    from app.main import app

    app.dependency_overrides[get_db] = override
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable"}
