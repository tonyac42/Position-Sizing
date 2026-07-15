import json
from pathlib import Path

from tests.test_api import golden_prediction_request, request_json

ROOT = Path(__file__).resolve().parents[1]

GOLDEN_REQUESTS = [
    golden_prediction_request(),
    {
        "bankroll": 50000,
        "trade_type": "trading",
        "edge_source": "live_track_record",
        "sample_size": 400,
        "entry_price": 100,
        "stop_price": 95,
        "volatility": 0.1,
        "edge_estimate": {"expectancy_r": 0.4, "variance": 1},
    },
    {
        "bankroll": 100000,
        "trade_type": "premium",
        "edge_source": "live_track_record",
        "sample_size": 200,
        "structural_max_loss": 10,
        "payoff_structure": "capped",
        "edge_estimate": {"outcomes": [[0.9, 0.1], [0.1, -1]]},
    },
]


def load_schema():
    return json.loads((ROOT / "tools/llm_function_schema.json").read_text())


def validate_minimal(schema, request):
    for required in schema["required"]:
        assert required in request
    allowed = set(schema["properties"])
    assert set(request).issubset(allowed)


def test_schema_validates_golden_requests():
    schema = load_schema()
    for request in GOLDEN_REQUESTS:
        validate_minimal(schema, request)


def test_schema_allows_guessed_critical_but_api_refuses(api_server):
    schema = load_schema()
    body = golden_prediction_request()
    body["field_confidence"] = {"bankroll": "guessed"}
    validate_minimal(schema, body)
    base, key, _ = api_server
    status, response = request_json(base, "/v1/size", body, key=key)
    assert status == 422
    assert json.loads(response)["refused"] is True
