from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oink.app import create_app
from oink.core.config import Settings

ADMIN = "admin"
PASSWORD = "una-password-lunga"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        admin_password=PASSWORD,
        ai_model="",
        enable_banking_key_file=tmp_path / "none.pem",
        update_check=False,
        public_url="https://oink.test",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, background=False)
    with TestClient(app, base_url="https://oink.test") as c:
        yield c


@pytest.fixture
def owner(client: TestClient) -> TestClient:
    reply = client.post("/api/auth/login", json={"username": ADMIN, "password": PASSWORD})
    assert reply.status_code == 200, reply.text
    return client


@pytest.fixture
def second(owner: TestClient, settings: Settings) -> TestClient:
    """Another user of the same instance, invited by the admin, in their own browser."""
    invite = owner.post("/api/users", json={"username": "marta"}).json()["invite_url"]
    token = invite.rsplit("/", 1)[1]
    other = TestClient(owner.app, base_url="https://oink.test")
    reply = other.post("/api/auth/invite", json={"token": token, "password": "marta-password-1"})
    assert reply.status_code == 200, reply.text
    return other


def token(browser: TestClient, *scopes: str) -> dict[str, str]:
    reply = browser.post("/api/tokens", json={"name": "assistant", "scopes": list(scopes)})
    assert reply.status_code == 200, reply.text
    return {"Authorization": f"Bearer {reply.json()['token']}"}
