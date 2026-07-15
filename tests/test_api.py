import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def api_server(tmp_path):
    port = free_port()
    db = tmp_path / "api.sqlite3"
    env = os.environ.copy()
    env.update(
        {
            "SIZER_DB": str(db),
            "PYTHONPATH": f"{ROOT / 'packages/sizer_engine'}:{ROOT / 'apps/api'}",
        }
    )
    key = subprocess.check_output(
        [
            sys.executable,
            "-m",
            "sizer_api.main",
            "create-dev-key",
            "--name",
            "pytest-full",
            "--scopes",
            "size:read,portfolio:write,track:write",
        ],
        cwd=ROOT,
        env=env,
        text=True,
    ).strip()
    size_only_key = subprocess.check_output(
        [
            sys.executable,
            "-m",
            "sizer_api.main",
            "create-dev-key",
            "--name",
            "pytest-size",
            "--scopes",
            "size:read",
        ],
        cwd=ROOT,
        env=env,
        text=True,
    ).strip()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "sizer_api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            ready = urllib.request.Request(
                f"{base}/v1/instruments/ES",
                headers={"authorization": f"Bearer {key}"},
            )
            urllib.request.urlopen(ready, timeout=0.2)
        except Exception:
            time.sleep(0.1)
        else:
            break
    yield base, key, size_only_key
    proc.terminate()
    proc.wait(timeout=5)


def request_json(base, path, body=None, key=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req_headers = {"content-type": "application/json", **(headers or {})}
    if key:
        req_headers["authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        base + path, data=data, headers=req_headers, method="POST" if body is not None else "GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def golden_prediction_request():
    return {
        "bankroll": 10000,
        "trade_type": "prediction",
        "payoff_structure": "binary",
        "edge_estimate": {"win_probability": 0.50},
        "edge_source": "guess",
        "sample_size": 120,
        "entry_price": 0.30,
        "structural_max_loss": 0.30,
        "expected_time_in_trade_days": 180,
        "exploration_override": True,
    }


def test_valid_key_with_scope_200(api_server):
    base, key, _ = api_server
    status, body = request_json(base, "/v1/size", golden_prediction_request(), key=key)
    assert status == 200
    assert json.loads(body)["explanation"]["binding_constraint"]


def test_valid_key_missing_scope_403(api_server):
    base, _, size_key = api_server
    status, _ = request_json(
        base, "/v1/track-record", {"strategy_id": "x", "outcome_r": 1}, key=size_key
    )
    assert status == 403


def test_missing_invalid_or_forged_key_401(api_server):
    base, _, _ = api_server
    status, _ = request_json(base, "/v1/size", golden_prediction_request())
    assert status == 401
    status, _ = request_json(
        base,
        "/v1/size",
        golden_prediction_request(),
        headers={"x-scopes": "size:read,portfolio:write"},
    )
    assert status == 401
    status, _ = request_json(base, "/v1/size", golden_prediction_request(), key="bad")
    assert status == 401


def test_malformed_body_400(api_server):
    base, key, _ = api_server
    body = golden_prediction_request()
    body["bankroll"] = -1
    status, response = request_json(base, "/v1/size", body, key=key)
    assert status == 400
    assert "field_errors" in response


def test_refusal_422(api_server):
    base, key, _ = api_server
    body = golden_prediction_request()
    body["field_confidence"] = {"bankroll": "guessed"}
    status, response = request_json(base, "/v1/size", body, key=key)
    payload = json.loads(response)
    assert status == 422
    assert payload["refused"] is True


def test_idempotency_replay_byte_identical(api_server):
    base, key, _ = api_server
    headers = {"Idempotency-Key": "same-key"}
    first_status, first_body = request_json(
        base, "/v1/size", golden_prediction_request(), key=key, headers=headers
    )
    second_status, second_body = request_json(
        base, "/v1/size", golden_prediction_request(), key=key, headers=headers
    )
    assert first_status == second_status == 200
    assert first_body == second_body


def test_track_record_drives_account_mode(api_server):
    base, key, _ = api_server
    for _ in range(40):
        status, _ = request_json(
            base, "/v1/track-record", {"strategy_id": "stored", "outcome_r": 0.6}, key=key
        )
        assert status == 200
    body = {
        "bankroll": 10000,
        "strategy_id": "stored",
        "sample_size": 0,
        "edge_estimate": {"expectancy_r": 0.1},
        "edge_source": "live_track_record",
        "structural_max_loss": 1,
        "per_trade_risk_cap": 1,
        "portfolio_heat_cap": 1,
        "correlation_bucket_cap": 1,
    }
    status, response = request_json(base, "/v1/size", body, key=key)
    payload = json.loads(response)
    assert status == 200
    assert payload["explanation"]["binding_constraint"] == "exploration_30_100"
    assert payload["explanation"]["working_edge_used"]["realized_mean_r"] == pytest.approx(0.6)
