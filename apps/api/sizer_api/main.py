from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from sizer_engine import size_trade
from sizer_engine.engine import input_hash
from sizer_engine.models import SizingRequest

LOGGER = logging.getLogger("sizer_api")
logging.basicConfig(level=logging.INFO, format="%(message)s")

DB = Path(os.environ.get("SIZER_DB", "sizer.sqlite3"))
DEV_MODE = os.environ.get("SIZER_DEV_MODE") == "1"
ALLOWED_ORIGINS = os.environ.get("SIZER_ALLOWED_ORIGINS", "http://localhost:5173").split(",")
DEV_KEY_PREFIX = "sizer_dev_"

app = FastAPI(title="Sizer API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

INSTRUMENTS = {
    "ES": {
        "volatility": 50,
        "liquidity": {"tier": "deep", "adv": 1_000_000_000},
        "correlation_bucket": "equity_index",
    },
    "POLY_ELECTION": {
        "volatility": 0.05,
        "liquidity": {"tier": "thin", "book_depth": 25_000},
        "correlation_bucket": "election",
    },
    "SPORTS_BOOK": {
        "volatility": 1,
        "liquidity": {"tier": "thin", "hard_limit": 15_000},
        "correlation_bucket": "sports_slate",
    },
}


def key_hash(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        create table if not exists api_keys(
            key_hash text primary key,
            name text not null,
            scopes text not null,
            created_at real not null,
            revoked integer not null default 0
        )
        """
    )
    connection.execute(
        """
        create table if not exists audit(
            ts real,
            request_id text,
            key_name text,
            input_hash text,
            binding_constraint text,
            duration_ms real,
            input text,
            output text
        )
        """
    )
    connection.execute(
        """
        create table if not exists portfolio(
            id integer primary key,
            instrument text,
            direction text,
            open_risk real,
            correlation_bucket text
        )
        """
    )
    connection.execute(
        """
        create table if not exists track_records(
            id integer primary key,
            strategy_id text not null,
            outcome_r real not null,
            created_at real not null
        )
        """
    )
    connection.execute(
        """
        create table if not exists idempotency(
            api_key_hash text not null,
            idempotency_key text not null,
            input_hash text not null,
            status_code integer not null,
            response_body text not null,
            created_at real not null,
            primary key(api_key_hash, idempotency_key, input_hash)
        )
        """
    )
    connection.commit()


@app.on_event("startup")
def startup() -> None:
    with connect():
        pass
    if DEV_MODE:
        LOGGER.warning("SIZER_DEV_MODE=1: AUTHENTICATION BYPASSED FOR LOCAL DEVELOPMENT")


def create_api_key(name: str, scopes: list[str]) -> str:
    raw_key = DEV_KEY_PREFIX + secrets.token_urlsafe(24)
    with connect() as connection:
        connection.execute(
            "insert into api_keys(key_hash, name, scopes, created_at, revoked) values (?, ?, ?, ?, 0)",
            (key_hash(raw_key), name, json.dumps(scopes), time.time()),
        )
    return raw_key


def get_key_record(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if DEV_MODE:
        return {"key_hash": "dev-mode", "name": "dev-mode", "scopes": ["*"]}
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="valid bearer API key required")
    raw_key = authorization.removeprefix("Bearer ").strip()
    hashed = key_hash(raw_key)
    with connect() as connection:
        row = connection.execute(
            "select key_hash, name, scopes from api_keys where key_hash = ? and revoked = 0",
            (hashed,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="valid bearer API key required")
    return {"key_hash": row["key_hash"], "name": row["name"], "scopes": json.loads(row["scopes"])}


def require(scope: str):
    def dependency(record: dict[str, Any] = Depends(get_key_record)) -> dict[str, Any]:
        if "*" not in record["scopes"] and scope not in record["scopes"]:
            raise HTTPException(status_code=403, detail=f"missing scope {scope}")
        return record

    return dependency


def parse_request_model(payload: dict[str, Any]) -> SizingRequest:
    try:
        return SizingRequest(**payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"field_errors": [str(exc)]}) from exc


def strategy_stats(strategy_id: str) -> tuple[int, float | None, list[float]]:
    with connect() as connection:
        rows = connection.execute(
            "select outcome_r from track_records where strategy_id = ? order by id",
            (strategy_id,),
        ).fetchall()
    outcomes = [float(row["outcome_r"]) for row in rows]
    if not outcomes:
        return 0, None, []
    return len(outcomes), sum(outcomes) / len(outcomes), outcomes


def enrich_with_track_record(req: SizingRequest) -> None:
    if not req.strategy_id:
        return
    count, mean_r, outcomes = strategy_stats(req.strategy_id)
    if count == 0 or mean_r is None:
        return
    req.sample_size = count
    req.realized_results = {"mean_r": mean_r, "outcomes": outcomes, "source": "stored_track_record"}


def response_for_sizing(req: SizingRequest) -> tuple[int, str, dict[str, Any]]:
    enrich_with_track_record(req)
    output = size_trade(req)
    if req.realized_results and req.realized_results.get("source") == "stored_track_record":
        output.explanation.setdefault("defaults_applied", []).append(
            "sample_size and realized_results overridden from stored track record"
        )
    status_code = 422 if output.refused else 200
    body = output.model_dump_json()
    return status_code, body, output.model_dump()


@app.post("/v1/size")
async def size_endpoint(
    request: Request,
    record: dict[str, Any] = Depends(require("size:read")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    start = time.perf_counter()
    request_id = request.headers.get("x-request-id", secrets.token_hex(8))
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail={"field_errors": ["invalid JSON body"]}
        ) from exc
    req = parse_request_model(payload)
    hashed_input = input_hash(req)

    if idempotency_key:
        with connect() as connection:
            existing = connection.execute(
                """
                select status_code, response_body from idempotency
                where api_key_hash = ? and idempotency_key = ? and input_hash = ?
                """,
                (record["key_hash"], idempotency_key, hashed_input),
            ).fetchone()
        if existing:
            return JSONResponse(
                json.loads(existing["response_body"]), status_code=existing["status_code"]
            )

    status_code, body, output_dict = response_for_sizing(req)
    if idempotency_key:
        with connect() as connection:
            connection.execute(
                "insert into idempotency values (?, ?, ?, ?, ?, ?)",
                (record["key_hash"], idempotency_key, hashed_input, status_code, body, time.time()),
            )

    duration_ms = (time.perf_counter() - start) * 1000
    binding = output_dict.get("explanation", {}).get("binding_constraint", "unknown")
    LOGGER.info(
        json.dumps(
            {
                "request_id": request_id,
                "key_name": record["name"],
                "input_hash": hashed_input,
                "binding_constraint": binding,
                "duration_ms": duration_ms,
            }
        )
    )
    with connect() as connection:
        connection.execute(
            "insert into audit values (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                time.time(),
                request_id,
                record["name"],
                hashed_input,
                binding,
                duration_ms,
                req.model_dump_json(),
                body,
            ),
        )
    return JSONResponse(json.loads(body), status_code=status_code)


@app.get("/v1/instruments/{instrument_id}")
def instrument(
    instrument_id: str, _: dict[str, Any] = Depends(require("size:read"))
) -> dict[str, Any]:
    if instrument_id not in INSTRUMENTS:
        raise HTTPException(status_code=404, detail="instrument not found")
    return INSTRUMENTS[instrument_id]


@app.post("/v1/scenarios/compare")
async def compare(
    request: Request,
    _: dict[str, Any] = Depends(require("size:read")),
) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, list):
        raise HTTPException(status_code=400, detail={"field_errors": ["body must be an array"]})
    results = [response_for_sizing(parse_request_model(item))[2] for item in payload]
    return JSONResponse(results)


@app.get("/v1/portfolio")
def portfolio(_: dict[str, Any] = Depends(require("size:read"))) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            "select id, instrument, direction, open_risk, correlation_bucket from portfolio"
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/v1/portfolio/position")
async def add_position(
    request: Request,
    _: dict[str, Any] = Depends(require("portfolio:write")),
) -> dict[str, Any]:
    position = await request.json()
    with connect() as connection:
        cursor = connection.execute(
            """
            insert into portfolio(instrument, direction, open_risk, correlation_bucket)
            values (?, ?, ?, ?)
            """,
            (
                position.get("instrument"),
                position.get("direction", "long"),
                position.get("open_risk", 0),
                position.get("correlation_bucket", "default"),
            ),
        )
    return {"id": cursor.lastrowid, **position}


@app.delete("/v1/portfolio/position/{position_id}")
def delete_position(
    position_id: int,
    _: dict[str, Any] = Depends(require("portfolio:write")),
) -> dict[str, int]:
    with connect() as connection:
        connection.execute("delete from portfolio where id = ?", (position_id,))
    return {"deleted": position_id}


@app.post("/v1/track-record")
async def track_record(
    request: Request,
    _: dict[str, Any] = Depends(require("track:write")),
) -> dict[str, Any]:
    """Log one realized strategy outcome.

    Expected body: {"strategy_id": "name", "outcome_r": 0.4}. outcome_r is the realized
    result in R-multiples: +1 means one unit of planned risk won, -1 means one unit lost.
    """
    record = await request.json()
    with connect() as connection:
        connection.execute(
            "insert into track_records(strategy_id, outcome_r, created_at) values (?, ?, ?)",
            (record["strategy_id"], float(record["outcome_r"]), time.time()),
        )
    count, mean_r, _ = strategy_stats(record["strategy_id"])
    return {
        "logged": True,
        "strategy_id": record["strategy_id"],
        "sample_size": count,
        "mean_r": mean_r,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sizer API utility commands")
    parser.add_argument("command", choices=["create-dev-key"])
    parser.add_argument("--name", default="dev")
    parser.add_argument(
        "--scopes",
        default="size:read,portfolio:write,track:write",
        help="Comma-separated scopes for the generated key.",
    )
    args = parser.parse_args()
    if args.command == "create-dev-key":
        key = create_api_key(args.name, [scope.strip() for scope in args.scopes.split(",")])
        print(key)


if __name__ == "__main__":
    main()
