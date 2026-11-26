"""Investments, plans (PAC), simulations, fixed costs, risk profile and the AI advisor."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from oink.api.deps import Caller, get_db, require
from oink.core.db import Account, FixedCost, Holding, Plan, Report, RiskProfile, Transaction
from oink.core.money import MoneyError, normalise_code
from oink.services import fixed as fixed_service
from oink.services import planning
from oink.services.ai import AI, AIError
from oink.services.markets import MarketError, history, quote, search
from oink.services.plans import (
    link_purchase,
    match_executions,
    plan_positions,
    proposals,
    suggestions,
    unlink_purchase,
)
from oink.services.reports import Book
from oink.services.simulate import (
    MonteCarlo,
    SimulationError,
    backtest,
    monthly_returns,
    portfolio_returns,
)

router = APIRouter(prefix="/api")
Kind = Literal["stock", "etf", "bond", "fund", "crypto", "index", "other"]
Frequency = Literal["weekly", "monthly", "quarterly", "yearly"]


def _dec(value: str | float | None, field: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except InvalidOperation as exc:
        raise HTTPException(422, f"{field}_invalid") from exc


def _ai(request: Request) -> AI:
    ai: AI = request.app.state.ai
    return ai


# -- market data -----------------------------------------------------------------------
@router.get("/markets/search", dependencies=[Depends(require("reports:read"))])
async def market_search(q: str = Query(min_length=1, max_length=60)) -> list[dict[str, str]]:
    try:
        return await search(q)
    except MarketError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/markets/quote/{symbol}", dependencies=[Depends(require("reports:read"))])
async def market_quote(symbol: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        q = await quote(db, symbol)
    except MarketError as exc:
        raise HTTPException(404 if str(exc) == "symbol_unknown" else 502, str(exc)) from exc
    return {"symbol": q.symbol, "price": str(q.price), "currency": q.currency, "name": q.name,
            "updated": q.updated}  # fmt: skip


@router.get("/markets/history/{symbol}", dependencies=[Depends(require("reports:read"))])
async def market_history(
    symbol: str, years: int = Query(default=10, ge=1, le=40), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    try:
        series = await history(db, symbol, years)
    except MarketError as exc:
        raise HTTPException(404, str(exc)) from exc
    return [{"day": d.isoformat(), "close": float(c)} for d, c in series[-years * 12 :]]


# -- holdings --------------------------------------------------------------------------
class HoldingIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    name: str = ""
    kind: Kind = "etf"
    currency: str = ""
    quantity: str | float
    cost: str | float | None = None
    manual_price: str | float | None = None


class HoldingPatch(BaseModel):
    quantity: str | float | None = None
    cost: str | float | None = None
    manual_price: str | float | None = None
    name: str | None = None


@router.get("/holdings")
async def holdings(
    who: Caller = Depends(require("accounts:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return await planning.holdings_value(db, Book.of(db, who.user))


@router.post("/holdings")
async def add_holding(
    body: HoldingIn, who: Caller = Depends(require("accounts:write")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    symbol = body.symbol.strip().upper()
    currency, name = body.currency, body.name
    if body.manual_price in (None, ""):
        try:
            q = await quote(db, symbol)
            currency, name = currency or q.currency, name or q.name
        except MarketError as exc:
            raise HTTPException(422, "symbol_unknown") from exc
    try:
        currency = normalise_code(currency or "EUR")
    except MoneyError as exc:
        raise HTTPException(422, "currency_invalid") from exc
    h = Holding(
        user_id=who.user_id,
        symbol=symbol,
        name=(name or symbol)[:160],
        kind=body.kind,
        currency=currency,
        quantity=_dec(body.quantity, "quantity") or Decimal(0),
        cost=_dec(body.cost, "cost"),
        manual_price=_dec(body.manual_price, "price"),
    )
    db.add(h)
    db.commit()
    return {"id": h.id}


def _holding(db: Session, who: Caller, holding_id: int) -> Holding:
    h = db.get(Holding, holding_id)
    if h is None or h.user_id != who.user_id:
        raise HTTPException(404, "holding_unknown")
    return h


@router.patch("/holdings/{holding_id}")
def update_holding(
    holding_id: int, body: HoldingPatch, who: Caller = Depends(require("accounts:write")),
    db: Session = Depends(get_db),
) -> dict[str, bool]:  # fmt: skip
    h = _holding(db, who, holding_id)
    data = body.model_dump(exclude_unset=True)
    for key in ("quantity", "cost", "manual_price"):
        if key in data:
            setattr(
                h,
                key,
                _dec(data[key], key) if key != "quantity" else _dec(data[key], key) or Decimal(0),
            )
    if data.get("name"):
        h.name = data["name"][:160]
    db.commit()
    return {"ok": True}


@router.delete("/holdings/{holding_id}")
def delete_holding(
    holding_id: int, who: Caller = Depends(require("accounts:write")), db: Session = Depends(get_db)
) -> dict[str, bool]:
    db.delete(_holding(db, who, holding_id))
    db.commit()
    return {"ok": True}


# -- plans (PAC) -----------------------------------------------------------------------
class PlanIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    name: str = ""
    amount: str | float
    currency: str = "EUR"
    frequency: Frequency = "monthly"
    start: date
    active: bool = True
    account_id: int | None = None
    day: int | None = Field(default=None, ge=1, le=31)
    end: date | None = None


def _plan_out(p: Plan, positions: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"id": p.id, "symbol": p.symbol, "name": p.name, "amount": str(p.amount),
            "currency": p.currency, "frequency": p.frequency, "start": p.start.isoformat(),
            "active": p.active, "account_id": p.account_id, "day": p.day,
            "end": p.end.isoformat() if p.end else None,
            "position": {k: v for k, v in (positions or {}).get(p.id, {}).items() if k != "days"} or None}  # fmt: skip


def _own_account_id(db: Session, who: Caller, account_id: int | None) -> int | None:
    if account_id is None:
        return None
    acc = db.get(Account, account_id)
    if acc is None or acc.user_id != who.user_id:
        raise HTTPException(404, "account_unknown")
    return acc.id


@router.get("/plans")
async def plans(
    who: Caller = Depends(require("accounts:read")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    positions = await plan_positions(db, who.user)
    return [
        _plan_out(p, positions) for p in db.scalars(select(Plan).where(Plan.user_id == who.user_id))
    ]


class PurchaseIn(BaseModel):
    plan_id: int


def _own_tx(db: Session, who: Caller, tx_id: int) -> Transaction:
    tx = db.get(Transaction, tx_id)
    if tx is None or tx.user_id != who.user_id:
        raise HTTPException(404, "transaction_unknown")
    return tx


@router.post("/transactions/{tx_id}/plan")
def mark_purchase(
    tx_id: int, body: PurchaseIn, who: Caller = Depends(require("transactions:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:  # fmt: skip
    """«This debit is a purchase of that plan»: an investment, and its units counted."""
    tx = _own_tx(db, who, tx_id)
    plan = db.get(Plan, body.plan_id)
    if plan is None or plan.user_id != who.user_id:
        raise HTTPException(404, "plan_unknown")
    if plan.account_id is None:
        plan.account_id = tx.account_id  # now Oink knows where this plan is paid from
    if plan.day is None:
        plan.day = tx.day.day
    link_purchase(db, tx, plan)
    match_executions(db, who.user)  # the plan's other months follow
    return {"ok": True, "plan_id": plan.id}


@router.delete("/transactions/{tx_id}/plan")
def unmark_purchase(
    tx_id: int, who: Caller = Depends(require("transactions:write")), db: Session = Depends(get_db)
) -> dict[str, bool]:
    unlink_purchase(db, _own_tx(db, who, tx_id))
    return {"ok": True}


@router.get("/plans/proposals")
async def plan_proposals(
    who: Caller = Depends(require("accounts:read")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """Plans Oink found by itself in the movements and statements, ready to add."""
    return await proposals(db, who.user)


@router.get("/plans/suggestions")
def plan_suggestions(
    who: Caller = Depends(require("accounts:read")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """Debits repeating every month with the same amount: probably a plan to set up."""
    return suggestions(db, who.user)


@router.post("/plans")
def add_plan(
    body: PlanIn, who: Caller = Depends(require("accounts:write")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    amount = _dec(body.amount, "amount")
    if amount is None or amount <= 0:
        raise HTTPException(422, "amount_invalid")
    try:
        currency = normalise_code(body.currency)
    except MoneyError as exc:
        raise HTTPException(422, "currency_invalid") from exc
    p = Plan(user_id=who.user_id, symbol=body.symbol.strip().upper(), name=body.name[:160],
             amount=amount, currency=currency, frequency=body.frequency, start=body.start,
             active=body.active, account_id=_own_account_id(db, who, body.account_id),
             day=body.day, end=body.end)  # fmt: skip
    db.add(p)
    db.commit()
    match_executions(db, who.user)
    return _plan_out(p)


class PlanItemIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    name: str = ""
    amount: str | float


class PlanBatchIn(BaseModel):
    """One savings plan of several securities («300 € on the 16th: 100 each»)."""

    account_id: int | None = None
    day: int | None = Field(default=None, ge=1, le=31)
    start: date
    frequency: Frequency = "monthly"
    currency: str = "EUR"
    items: list[PlanItemIn] = Field(min_length=1, max_length=20)


@router.post("/plans/batch")
def add_plans(
    body: PlanBatchIn,
    who: Caller = Depends(require("accounts:write")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    account_id = _own_account_id(db, who, body.account_id)
    created = []
    for item in body.items:
        amount = _dec(item.amount, "amount")
        if amount is None or amount <= 0:
            raise HTTPException(422, "amount_invalid")
        p = Plan(user_id=who.user_id, symbol=item.symbol.strip().upper(), name=item.name[:160],
                 amount=amount, currency=normalise_code(body.currency), frequency=body.frequency,
                 start=body.start, active=True, account_id=account_id, day=body.day)  # fmt: skip
        db.add(p)
        created.append(p)
    db.commit()
    match_executions(db, who.user)
    return [_plan_out(p) for p in created]


class ReplaceIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    name: str = ""
    start: date  # the first purchase of the new security


@router.post("/plans/{plan_id}/replace")
def replace_plan(
    plan_id: int, body: ReplaceIn, who: Caller = Depends(require("accounts:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:  # fmt: skip
    """Same plan, another security from a date (Nvidia → Anthropic from December): the old
    one ends the day before, the new one takes its amount, day and account."""
    old = db.get(Plan, plan_id)
    if old is None or old.user_id != who.user_id:
        raise HTTPException(404, "plan_unknown")
    old.end = body.start - timedelta(days=1)
    new = Plan(user_id=who.user_id, symbol=body.symbol.strip().upper(), name=body.name[:160],
               amount=old.amount, currency=old.currency, frequency=old.frequency, start=body.start,
               active=True, account_id=old.account_id, day=old.day)  # fmt: skip
    db.add(new)
    for tx in db.scalars(
        select(Transaction).where(Transaction.plan_id == old.id, Transaction.day > old.end)
    ):
        tx.plan_id = None  # purchases after the switch belong to the new security
    db.commit()
    match_executions(db, who.user)
    return _plan_out(new)


@router.patch("/plans/{plan_id}")
def update_plan(
    plan_id: int, body: PlanIn, who: Caller = Depends(require("accounts:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:  # fmt: skip
    p = db.get(Plan, plan_id)
    if p is None or p.user_id != who.user_id:
        raise HTTPException(404, "plan_unknown")
    p.symbol, p.name, p.frequency, p.start, p.active = (
        body.symbol.strip().upper(),
        body.name[:160],
        body.frequency,
        body.start,
        body.active,
    )
    p.amount = _dec(body.amount, "amount") or p.amount
    p.account_id, p.day, p.end = _own_account_id(db, who, body.account_id), body.day, body.end
    for tx in db.scalars(select(Transaction).where(Transaction.plan_id == p.id)):
        tx.plan_id = None  # matched again below, with the plan's new terms
        if tx.category_by == "plan":
            tx.category, tx.category_by = "", ""
    db.commit()
    match_executions(db, who.user)
    return _plan_out(p)


@router.delete("/plans/{plan_id}")
def delete_plan(
    plan_id: int, who: Caller = Depends(require("accounts:write")), db: Session = Depends(get_db)
) -> dict[str, bool]:
    p = db.get(Plan, plan_id)
    if p is None or p.user_id != who.user_id:
        raise HTTPException(404, "plan_unknown")
    for tx in db.scalars(select(Transaction).where(Transaction.plan_id == p.id)):
        tx.plan_id = None
        if tx.category_by == "plan":
            tx.category, tx.category_by = "", ""
    db.delete(p)
    db.commit()
    return {"ok": True}


# -- simulations -----------------------------------------------------------------------
class Asset(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    weight: float = Field(default=1, gt=0)


class SimulationIn(BaseModel):
    assets: list[Asset] = Field(min_length=1, max_length=10)
    initial: float = Field(default=0, ge=0)
    monthly: float = Field(default=0, ge=0)
    years: int = Field(default=10, ge=1, le=60)
    history_years: int = Field(default=20, ge=3, le=40)
    method: Literal["bootstrap", "gbm"] = "bootstrap"
    paths: int = Field(default=2000, ge=100, le=10000)
    inflation: float = Field(default=0.02, ge=0, le=0.2)
    target: float | None = None


@router.post("/simulate", dependencies=[Depends(require("reports:read"))])
async def simulate(body: SimulationIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Backtest on real history + Monte Carlo of the future, for a PAC, a lump sum or both,
    on one asset or a weighted portfolio."""
    if body.initial <= 0 and body.monthly <= 0:
        raise HTTPException(422, "amount_invalid")
    try:
        series = [await history(db, a.symbol, body.history_years) for a in body.assets]
    except MarketError as exc:
        raise HTTPException(404, str(exc)) from exc
    try:
        if len(series) == 1:
            returns, past = monthly_returns(series[0]), series[0]
        else:
            returns = portfolio_returns(series, [a.weight for a in body.assets])
            past = _portfolio_series(series, [a.weight for a in body.assets])
        result = {
            "backtest": backtest(past, body.monthly, body.initial),
            "monte_carlo": MonteCarlo(
                returns,
                body.years,
                body.initial,
                body.monthly,
                body.paths,
                body.method,
                body.inflation,
                body.target,
            ).run(),
            "history_from": past[0][0].isoformat(),
        }
    except SimulationError as exc:
        raise HTTPException(422, str(exc)) from exc
    return result


def _portfolio_series(
    series: list[list[tuple[date, Decimal]]], weights: list[float]
) -> list[tuple[date, Decimal]]:
    """An index (100 at start) of the monthly-rebalanced portfolio, for the backtest."""
    maps = [dict(s) for s in series]
    months = sorted(set.intersection(*(set(m) for m in maps)))
    returns = portfolio_returns(series, weights)
    level, out = Decimal(100), [(months[0], Decimal(100))]
    for month, r in zip(months[1:], returns, strict=False):
        level = level * Decimal(str(1 + r))
        out.append((month, level))
    return out


# -- fixed costs -----------------------------------------------------------------------
class FixedIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    amount: str | float
    currency: str = "EUR"
    frequency: Frequency = "monthly"
    category: str = ""
    next_due: date | None = None
    match: str = ""
    active: bool = True


@router.get("/fixed-costs")
def get_fixed(
    who: Caller = Depends(require("reports:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return planning.fixed_costs(db, Book.of(db, who.user))


def _fixed_values(f: FixedCost, body: FixedIn) -> None:
    amount = _dec(body.amount, "amount")
    if amount is None or amount <= 0:
        raise HTTPException(422, "amount_invalid")
    try:
        f.currency = normalise_code(body.currency)
    except MoneyError as exc:
        raise HTTPException(422, "currency_invalid") from exc
    f.name, f.amount, f.frequency = body.name, abs(amount), body.frequency
    f.category, f.next_due, f.match, f.active = (
        body.category.strip().lower(),
        body.next_due,
        body.match.strip().lower(),
        body.active,
    )


@router.post("/fixed-costs")
def add_fixed(
    body: FixedIn, who: Caller = Depends(require("accounts:write")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    f = FixedCost(user_id=who.user_id, name=body.name, amount=Decimal(0))
    _fixed_values(f, body)
    # the same cost again (marked twice from the movements): keep the one there is
    key = (f.match or f.name).strip().lower()
    for other in db.scalars(select(FixedCost).where(FixedCost.user_id == who.user_id)):
        if (other.match or other.name).strip().lower() == key and abs(other.amount - f.amount) < Decimal("0.01") \
                and other.frequency == f.frequency:  # fmt: skip
            return {"id": other.id, "duplicate": True}
    db.add(f)
    db.commit()
    return {"id": f.id}


@router.get("/fixed-costs/missing")
def missing_fixed(
    who: Caller = Depends(require("accounts:read")), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    """Fixed costs whose payment didn't come when expected."""
    return fixed_service.refresh(db, who.user)


@router.get("/fixed-costs/{cost_id}/payments")
def fixed_payments(
    cost_id: int, who: Caller = Depends(require("accounts:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    f = db.get(FixedCost, cost_id)
    if f is None or f.user_id != who.user_id:
        raise HTTPException(404, "fixed_unknown")
    fixed_service.refresh(db, who.user)  # due date moved past the payments that came
    return fixed_service.history(db, who.user, f)


class SnoozeIn(BaseModel):
    days: int = Field(default=7, ge=1, le=90)


@router.post("/fixed-costs/{cost_id}/wait")
def wait_fixed(
    cost_id: int, body: SnoozeIn, who: Caller = Depends(require("accounts:write")),
    db: Session = Depends(get_db),
) -> dict[str, bool]:  # fmt: skip
    """«It will come»: don't flag it again for a while."""
    f = db.get(FixedCost, cost_id)
    if f is None or f.user_id != who.user_id:
        raise HTTPException(404, "fixed_unknown")
    f.snoozed_until = date.today() + timedelta(days=body.days)
    db.commit()
    return {"ok": True}


@router.post("/fixed-costs/{cost_id}/stop")
def stop_fixed(
    cost_id: int, who: Caller = Depends(require("accounts:write")), db: Session = Depends(get_db)
) -> dict[str, bool]:
    """«This cost is over» (cancelled subscription): kept in the history, no longer expected."""
    f = db.get(FixedCost, cost_id)
    if f is None or f.user_id != who.user_id:
        raise HTTPException(404, "fixed_unknown")
    f.active = False
    db.commit()
    return {"ok": True}


class SplitPart(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    amount: str | float
    frequency: Frequency = "monthly"
    next_due: date | None = None
    category: str | None = None


class SplitIn(BaseModel):
    parts: list[SplitPart] = Field(min_length=2, max_length=10)


@router.post("/fixed-costs/{cost_id}/split")
def split_fixed(
    cost_id: int, body: SplitIn, who: Caller = Depends(require("accounts:write")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:  # fmt: skip
    """One payment, several costs (a server monthly and a domain yearly paid together):
    the cost becomes its parts, each with its own amount and frequency, same payee."""
    f = db.get(FixedCost, cost_id)
    if f is None or f.user_id != who.user_id:
        raise HTTPException(404, "fixed_unknown")
    ids = []
    for part in body.parts:
        amount = _dec(part.amount, "amount")
        if amount is None or amount <= 0:
            raise HTTPException(422, "amount_invalid")
        new = FixedCost(user_id=who.user_id, name=part.name, amount=abs(amount), currency=f.currency,
                        frequency=part.frequency, category=(part.category or f.category), match=f.match or f.name.lower(),
                        next_due=part.next_due or f.next_due, active=True)  # fmt: skip
        db.add(new)
        db.flush()
        ids.append(new.id)
    db.delete(f)
    db.commit()
    return {"ids": ids}


@router.put("/fixed-costs/{cost_id}")
def update_fixed(
    cost_id: int, body: FixedIn, who: Caller = Depends(require("accounts:write")),
    db: Session = Depends(get_db),
) -> dict[str, bool]:  # fmt: skip
    f = db.get(FixedCost, cost_id)
    if f is None or f.user_id != who.user_id:
        raise HTTPException(404, "fixed_unknown")
    _fixed_values(f, body)
    db.commit()
    return {"ok": True}


@router.delete("/fixed-costs/{cost_id}")
def delete_fixed(
    cost_id: int, who: Caller = Depends(require("accounts:write")), db: Session = Depends(get_db)
) -> dict[str, bool]:
    f = db.get(FixedCost, cost_id)
    if f is None or f.user_id != who.user_id:
        raise HTTPException(404, "fixed_unknown")
    db.delete(f)
    db.commit()
    return {"ok": True}


# -- risk profile, the whole picture, advice -------------------------------------------
class ProfileIn(BaseModel):
    answers: dict[str, int]
    horizon_years: int = Field(default=10, ge=1, le=60)
    goals: str = Field(default="", max_length=2000)


@router.get("/profile/risk")
def get_risk(
    who: Caller = Depends(require("reports:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    p = db.get(RiskProfile, who.user_id)
    return {
        "questions": list(planning.QUESTIONS),
        "answers": p.answers if p else {},
        "score": p.score if p else None,
        "profile": planning.PROFILES.get(p.score) if p else None,
        "horizon_years": p.horizon_years if p else 10,
        "goals": p.goals if p else "",
        "equity_share": planning.EQUITY_SHARE.get(p.score) if p else None,
    }


@router.put("/profile/risk")
def set_risk(
    body: ProfileIn, who: Caller = Depends(require("accounts:write")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    answers = {
        k: min(5, max(1, int(v))) for k, v in body.answers.items() if k in planning.QUESTIONS
    }
    p = db.get(RiskProfile, who.user_id) or RiskProfile(user_id=who.user_id)
    p.answers, p.score, p.horizon_years, p.goals = (
        answers,
        planning.score(answers),
        body.horizon_years,
        body.goals,
    )
    p.updated = datetime.now(UTC)
    db.merge(p)
    db.commit()
    return get_risk(who, db)


@router.get("/picture")
async def picture(
    who: Caller = Depends(require("reports:read")), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """The whole picture: net worth, liquidity, investments, plans, fixed costs, lifestyle,
    risk profile."""
    return await planning.picture(db, Book.of(db, who.user))


class AdviceIn(BaseModel):
    focus: str = Field(default="", max_length=500)
    language: str = Field(default="", max_length=8)


@router.post("/advice")
async def advice(
    body: AdviceIn, request: Request, who: Caller = Depends(require("ai:ask")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:  # fmt: skip
    ai = _ai(request)
    facts = await planning.picture(db, Book.of(db, who.user))
    language = body.language or who.user.language or "en"
    try:
        content = await ai.advise(facts, language, body.focus)
    except AIError as exc:
        raise HTTPException(503, str(exc).split(":")[0]) from exc
    report = Report(user_id=who.user_id, month=date.today().strftime("%Y-%m"), kind="advice",
                    content=content, model=ai.config().model, language=language)  # fmt: skip
    db.add(report)
    db.commit()
    return {"id": report.id, "content": content, "created": report.created}
