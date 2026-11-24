from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oink.app import create_app
from oink.core.config import Settings

PASSWORD = "una-password-lunga"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        admin_password=PASSWORD,
        ai_model="",
        enable_banking_key_file=tmp_path / "none.pem",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings, background=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def owner(client: TestClient) -> TestClient:
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    return client


def token(owner: TestClient, *scopes: str) -> dict[str, str]:
    reply = owner.post("/api/tokens", json={"name": "jarvis", "scopes": list(scopes)})
    assert reply.status_code == 200, reply.text
    return {"Authorization": f"Bearer {reply.json()['token']}"}
