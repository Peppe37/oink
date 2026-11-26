"""Oink 0.3: markets, simulations, holdings, fixed costs, risk profile, AI settings, SSO, MCP."""

from __future__ import annotations

import json
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from oink.core.config import Settings
from oink.core.db import Database, Price, Quote
from oink.core.oidc import OIDC
from oink.services import markets
from oink.services.planning import score
from oink.services.simulate import MonteCarlo, backtest, monthly_returns, portfolio_returns
from tests.conftest import token


def series(start: float, growth: float, months: int) -> list[tuple[date, Decimal]]:
    out, price = [], start
    for i in range(months):
        y, m = divmod(i, 12)
        out.append((date(2005 + y, m + 1, 1), Decimal(str(round(price, 4)))))
        price *= 1 + growth + (0.03 if i % 7 == 0 else -0.01)
    return out


def test_backtest_of_a_pac() -> None:
    flat = [(date(2020, m, 1), Decimal(10)) for m in range(1, 13)]
    out = backtest(flat, monthly=100, initial=1000)
    assert out["invested"] == 2200 and out["value"] == 2200 and out["max_drawdown"] == 0
    rising = backtest(series(10, 0.01, 60), monthly=100)
    assert rising["value"] > rising["invested"] and rising["return"] > 0


def test_monte_carlo_bands_are_ordered_and_reproducible() -> None:
    returns = monthly_returns(series(10, 0.006, 240))
    a = MonteCarlo(returns, 10, 1000, 200, paths=500, target=40000).run()
    b = MonteCarlo(returns, 10, 1000, 200, paths=500, target=40000).run()
    assert a == b and len(a["bands"]) == 11
    last = a["bands"][-1]
    assert last["p5"] <= last["p25"] <= last["p50"] <= last["p75"] <= last["p95"]
    assert a["invested"] == 1000 + 200 * 120 and 0 <= a["prob_loss"] <= 1 and "prob_target" in a
    gbm = MonteCarlo(returns, 5, 0, 100, paths=300, method="gbm").run()
    assert gbm["median"] > 0


def test_portfolio_returns_mix_assets() -> None:
    a, b = series(10, 0.01, 36), series(50, 0.0, 36)
    mixed = portfolio_returns([a, b], [1, 1])
    ra, rb = monthly_returns(a), monthly_returns(b)
    assert mixed[3] == pytest.approx((ra[3] + rb[3]) / 2)


def test_risk_score() -> None:
    assert score({"horizon": 5, "drop": 5, "experience": 4, "income": 5, "goal": 5}) == 5
    assert score({"horizon": 1, "drop": 1}) == 1 and score({}) == 3


async def test_quote_and_history_from_yahoo(tmp_path: Path) -> None:
    db = Database(f"sqlite:///{tmp_path / 'm.db'}")
    db.create()

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("interval") == "1mo":
            return httpx.Response(200, json={"chart": {"result": [{
                "meta": {}, "timestamp": [1704067200, 1706745600],
                "indicators": {"adjclose": [{"adjclose": [100.5, 101.25]}]}}]}})  # fmt: skip
        return httpx.Response(200, json={"chart": {"result": [{"meta": {
            "regularMarketPrice": 1234.5, "currency": "GBp", "longName": "Fund"}}]}})  # fmt: skip

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        with db.sessions() as s:
            q = await markets.quote(s, "abc.l", client)
            assert q.symbol == "ABC.L" and q.currency == "GBP" and q.price == Decimal("12.345")
            hist = await markets.history(s, "abc.l", 5, client)
            assert [c for _, c in hist] == [Decimal("100.5"), Decimal("101.25")]


def test_holdings_fixed_costs_profile_and_picture(owner: TestClient) -> None:
    owner.post("/api/accounts", json={"name": "Main", "balance": "6000"})
    r = owner.post("/api/holdings", json={"symbol": "my-bond", "kind": "bond", "currency": "EUR",
                                          "quantity": "10", "cost": "900", "manual_price": "95.5"})  # fmt: skip
    assert r.status_code == 200, r.text
    h = owner.get("/api/holdings").json()
    assert h["total"] == 955.0 and h["items"][0]["gain"] == 55.0 and h["by_kind"] == {"bond": 955.0}
    owner.post("/api/fixed-costs", json={"name": "Rent", "amount": "600"})
    owner.post(
        "/api/fixed-costs", json={"name": "Insurance", "amount": "360", "frequency": "yearly"}
    )
    fixed = owner.get("/api/fixed-costs").json()
    assert fixed["monthly"] == 630.0 and fixed["yearly"] == 7560.0
    prof = owner.put("/api/profile/risk", json={"answers": {"horizon": 4, "drop": 3, "experience": 3,
                                                            "income": 4, "goal": 4}, "horizon_years": 15}).json()  # fmt: skip
    assert prof["score"] == 4 and prof["profile"] == "growth"
    owner.post("/api/plans", json={"symbol": "VWCE.DE", "amount": "200", "start": "2026-01-01"})
    pic = owner.get("/api/picture").json()
    assert pic["net_worth"] == 6955.0 and pic["fixed_costs_monthly"] == 630.0
    assert pic["plans_monthly"] == 200.0 and pic["risk"]["suggested_equity_share"] == 0.75
    assert owner.post("/api/advice", json={}).status_code == 503  # no AI configured


def test_simulation_endpoint_with_cached_history(owner: TestClient) -> None:
    with owner.app.state.db.sessions() as s:  # type: ignore[attr-defined]
        for d, c in series(20, 0.006, 120):
            s.add(Price(symbol="TEST", day=d, close=c))
        s.add(Quote(symbol="H:TEST", price=Decimal(0)))
        s.commit()
    r = owner.post("/api/simulate", json={"assets": [{"symbol": "TEST"}], "monthly": 100,
                                          "years": 5, "paths": 200})  # fmt: skip
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["backtest"]["invested"] == 100 * 120 and len(out["monte_carlo"]["bands"]) == 6
    bad = owner.post("/api/simulate", json={"assets": [{"symbol": "TEST"}], "monthly": 0})
    assert bad.status_code == 422


def test_ai_settings_from_the_app(admin: TestClient, second: TestClient) -> None:
    assert admin.get("/api/system/ai").json()["source"] == "off"
    saved = admin.put(
        "/api/system/ai", json={"model": "openai/gpt-5.4-mini", "api_key": "sk-test-123"}
    ).json()
    assert saved["source"] == "app" and saved["has_key"] and saved["model"] == "openai/gpt-5.4-mini"
    assert admin.get("/api/auth/status").json()["ai"] is True
    with admin.app.state.db.sessions() as s:  # type: ignore[attr-defined]
        from oink.core.db import Setting

        assert "sk-test-123" not in s.get(Setting, "ai.api_key").value  # encrypted
    assert admin.app.state.ai.config().api_key == "sk-test-123"  # type: ignore[attr-defined]
    assert second.put("/api/system/ai", json={"model": "x/y-z"}).status_code == 403
    assert admin.delete("/api/system/ai").json()["source"] == "off"


def test_mcp_tools_follow_token_scopes(owner: TestClient) -> None:
    owner.post("/api/accounts", json={"name": "Main", "balance": "10"})
    read = token(owner, "accounts:read", "reports:read")
    init = owner.post(
        "/mcp", headers=read, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert init.json()["result"]["serverInfo"]["name"] == "oink"
    names = {t["name"] for t in owner.post("/mcp", headers=read, json={"jsonrpc": "2.0", "id": 2,
             "method": "tools/list"}).json()["result"]["tools"]}  # fmt: skip
    assert "list_accounts" in names and "add_transaction" not in names
    call = owner.post("/mcp", headers=read, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "list_accounts", "arguments": {}}}).json()  # fmt: skip
    assert json.loads(call["result"]["content"][0]["text"])[0]["balance"] == "10.00"
    denied = owner.post("/mcp", headers=read, json={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": "add_transaction", "arguments": {}}}).json()  # fmt: skip
    assert "error" in denied
    assert owner.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    note = owner.post(
        "/mcp", headers=read, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert note.status_code == 202


class FakeIdP:
    """A minimal OpenID provider: discovery, token endpoint, JWKS."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.nonce = ""
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwks = {"keys": [{**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}]}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("openid-configuration"):
            return httpx.Response(200, json={"issuer": "https://idp.test", "authorization_endpoint": "https://idp.test/auth",
                                             "token_endpoint": "https://idp.test/token", "jwks_uri": "https://idp.test/jwks"})  # fmt: skip
        if path == "/token":
            claims = {"iss": "https://idp.test", "aud": "oink", "sub": "abc-123", "nonce": self.nonce,
                      "exp": int(time.time()) + 300, "iat": int(time.time()), "preferred_username": "Marta.R",
                      "email": "m@example.test", "groups": ["oink-admins"]}  # fmt: skip
            return httpx.Response(
                200, json={"id_token": jwt.encode(claims, self.key, "RS256", headers={"kid": "k1"})}
            )
        return httpx.Response(404)


async def test_oidc_login_creates_the_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    idp = FakeIdP()
    settings = Settings(data_dir=tmp_path, oidc_issuer="https://idp.test", oidc_client_id="oink",
                        oidc_admin_group="oink-admins", public_url="https://oink.test")  # fmt: skip
    async with httpx.AsyncClient(transport=httpx.MockTransport(idp)) as client:
        oidc = OIDC(settings, client)
        url = await oidc.start()
        assert url.startswith("https://idp.test/auth?") and "code_challenge_method=S256" in url
        state = url.split("state=")[1].split("&")[0]
        idp.nonce = oidc.pending[state].nonce
        monkeypatch.setattr(jwt, "PyJWKClient", lambda uri: _StaticJWKS(idp))
        claims, link = await oidc.finish(state, "code-1")
        assert claims["sub"] == "abc-123" and link is None and oidc.is_admin(claims)
        with pytest.raises(Exception):  # noqa: B017 - a state works once
            await oidc.finish(state, "code-1")


class _StaticJWKS:
    def __init__(self, idp: FakeIdP) -> None:
        self.idp = idp

    def get_signing_key_from_jwt(self, token: str) -> Any:
        return jwt.PyJWK(self.idp.jwks["keys"][0])
