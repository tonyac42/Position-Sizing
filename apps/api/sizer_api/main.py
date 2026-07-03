from __future__ import annotations

import json, sqlite3, time
from pathlib import Path
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sizer_engine import size_trade
from sizer_engine.models import SizingRequest

app = FastAPI(title="Sizer API", version="0.1.0")
DB = Path("sizer.sqlite3")
INSTRUMENTS = {
    "ES": {"volatility": 50, "liquidity": {"tier":"deep", "adv": 1000000000}, "correlation_bucket":"equity_index"},
    "POLY_ELECTION": {"volatility": 0.05, "liquidity": {"tier":"thin", "book_depth": 25000}, "correlation_bucket":"election"},
    "SPORTS_BOOK": {"volatility": 1, "liquidity": {"tier":"thin", "hard_limit": 15000}, "correlation_bucket":"sports_slate"},
}

def db():
    con=sqlite3.connect(DB); con.execute("create table if not exists audit(ts real,input_hash text,interface text,input text,output text)"); con.execute("create table if not exists portfolio(id integer primary key,instrument text,direction text,open_risk real,correlation_bucket text)"); con.execute("create table if not exists track(strategy_id text,outcome real,ts real)"); return con

def auth(x_api_key: str = Header(default="dev-key"), x_scopes: str = Header(default="size:read,portfolio:write,track:write")):
    if not x_api_key: raise HTTPException(401,"API key required")
    return set(s.strip() for s in x_scopes.split(","))

def require(scope):
    def dep(scopes=Depends(auth)):
        if scope not in scopes: raise HTTPException(403, f"missing scope {scope}")
    return dep

@app.post("/v1/size", dependencies=[Depends(require("size:read"))])
async def size(req: SizingRequest, request: Request, idempotency_key: str | None = Header(default=None)):
    out=size_trade(req)
    con=db(); con.execute("insert into audit values (?,?,?,?,?)",(time.time(),out.metadata.get("input_hash"), request.headers.get("x-interface","api"), req.model_dump_json(), out.model_dump_json())); con.commit(); con.close()
    if out.refused: return JSONResponse(out.model_dump(), status_code=422)
    return out

@app.get("/v1/instruments/{instrument_id}")
def instrument(instrument_id: str, _=Depends(require("size:read"))):
    if instrument_id not in INSTRUMENTS: raise HTTPException(404,"instrument not found")
    return INSTRUMENTS[instrument_id]

@app.post("/v1/scenarios/compare", dependencies=[Depends(require("size:read"))])
def compare(requests: list[SizingRequest]):
    return [size_trade(r) for r in requests]

@app.get("/v1/portfolio", dependencies=[Depends(require("size:read"))])
def portfolio():
    con=db(); rows=con.execute("select id,instrument,direction,open_risk,correlation_bucket from portfolio").fetchall(); con.close(); return [{"id":r[0],"instrument":r[1],"direction":r[2],"open_risk":r[3],"correlation_bucket":r[4]} for r in rows]

@app.post("/v1/portfolio/position", dependencies=[Depends(require("portfolio:write"))])
def add_position(pos: dict):
    con=db(); cur=con.execute("insert into portfolio(instrument,direction,open_risk,correlation_bucket) values (?,?,?,?)",(pos.get("instrument"),pos.get("direction","long"),pos.get("open_risk",0),pos.get("correlation_bucket","default"))); con.commit(); con.close(); return {"id":cur.lastrowid, **pos}

@app.delete("/v1/portfolio/position/{position_id}", dependencies=[Depends(require("portfolio:write"))])
def delete_position(position_id: int):
    con=db(); con.execute("delete from portfolio where id=?",(position_id,)); con.commit(); con.close(); return {"deleted":position_id}

@app.post("/v1/track-record", dependencies=[Depends(require("track:write"))])
def track(record: dict):
    con=db(); con.execute("insert into track values (?,?,?)",(record["strategy_id"], record["outcome"], time.time())); con.commit(); con.close(); return {"logged": True}
