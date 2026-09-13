from __future__ import annotations

from tools.collecteur_microstructure import (
    parse_binance,
    parse_bybit,
    parse_okx,
)


def test_parse_binance_normalizes_book_and_aggressor_side():
    venue = parse_binance("BTCUSDT", {
        "lastUpdateId": 10,
        "bids": [["100", "2"]],
        "asks": [["101", "3"]],
    }, [
        {"a": 1, "p": "101", "q": "0.5", "T": 1000, "m": False},
        {"a": 2, "p": "100", "q": "0.4", "T": 1001, "m": True},
    ], received_ms=1010)

    assert venue.venue == "binance"
    assert venue.bids == ((100.0, 2.0),)
    assert [trade[1] for trade in venue.trades] == ["buy", "sell"]


def test_parse_bybit_and_okx_normalize_public_responses():
    bybit = parse_bybit("BTCUSDT", {
        "retCode": 0,
        "result": {"b": [["100", "2"]], "a": [["101", "3"]], "ts": 1010},
    }, {
        "retCode": 0,
        "result": {"list": [
            {"price": "101", "size": "0.5", "side": "Buy", "time": "1000"},
        ]},
    }, received_ms=1020)
    okx = parse_okx("BTCUSDT", {
        "code": "0",
        "data": [{"bids": [["100", "2", "0", "1"]],
                  "asks": [["101", "3", "0", "1"]], "ts": "1011"}],
    }, {
        "code": "0",
        "data": [{"px": "100", "sz": "0.4", "side": "sell", "ts": "1001"}],
    }, received_ms=1020)

    assert bybit.venue == "bybit" and bybit.trades[0][1] == "buy"
    assert okx.venue == "okx" and okx.trades[0][1] == "sell"

