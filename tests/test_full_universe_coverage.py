import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from titanium import edge
from titanium.analysis.quote_coverage import coverage_for
from titanium.organism.trading_knowledge import knowledge_for
from tools import enregistreur_quotes as quotes
from tools.audit_quotes_sorties import main


def test_catalogue_includes_hidden_and_more_than_149_assets(monkeypatch):
    specs = [SimpleNamespace(name=f"ASSET{i}", visible=i < 2) for i in range(173)]
    @contextmanager
    def session():
        yield SimpleNamespace(symbols_get=lambda: specs + specs[:2])
    monkeypatch.setattr(quotes, "mt5_session", session)
    assert quotes.univers_portable() == [s.name for s in specs]


@pytest.mark.parametrize("symbol,group,expected", [
    ("AAVE-USD", "ROW_CRYPTO\\CRYPTO", "crypto"),
    ("AUDCAD", "ROW_STANDARD_FX\\FX", "fx"),
    ("COPPER.fs", "ROW_FUTURES\\FUT_COMMODITY", "metaux"),
    ("BRENT.fs", "ROW_FUTURES\\FUT_COMMODITY", "energie"),
    ("DAX40.fs", "ROW_FUTURES\\FUT_INDICES", "indices"),
    ("WHEAT.fs", "ROW_FUTURES\\FUT_COMMODITY", "agricole"),
])
def test_hermes_uses_broker_classification_beyond_legacy_list(monkeypatch, symbol, group, expected):
    monkeypatch.setattr(edge, "_groupe_mt5", lambda _: group)
    book = knowledge_for(symbol)
    assert book["asset_class"] == expected
    assert book["classification_status"] == "KNOWN"
    assert "Instrument mapping" not in book["market_context"]


def test_unknown_class_is_explicit_and_requires_wait():
    book = knowledge_for("NEW_INSTRUMENT", "unclassified")
    assert book["classification_status"] == "UNKNOWN"
    assert "requires WAIT" in book["decision"]


def test_known_class_does_not_contact_broker(monkeypatch):
    monkeypatch.setattr(edge, "asset_class_of", lambda _: pytest.fail("already classified"))
    assert knowledge_for("WHEAT.fs", "agricole")["classification_status"] == "KNOWN"


def test_coverage_retains_every_missing_asset(tmp_path):
    symbols = [f"ASSET{i}" for i in range(173)]
    folder = tmp_path / "ASSET0"
    folder.mkdir()
    (folder / "2026-09-07.ndjson").write_text(json.dumps({
        "symbole": "ASSET0", "horloge": "utc", "ts_ms": 1000,
    }) + "\n")
    result = coverage_for(symbols, tmp_path, now_ms=2000)
    assert result["expected_symbols"] == 173
    assert result["counts"] == {"ARCHIVE_PRESENT": 1, "NO_ARCHIVE": 172}
    assert result["assets"][0]["archive_age_ms"] == 1000
    assert result["ready_for_exit_optimization"] is False
    assert result["assets"][0]["quality"] == "NOT_AUDITED"


@pytest.mark.parametrize("symbol", ["..", "../foo", "C:\\foo", "foo/bar", ""])
def test_coverage_rejects_unsafe_symbol(tmp_path, symbol):
    with pytest.raises(ValueError):
        coverage_for([symbol], tmp_path)


def test_truncated_or_future_tail_is_not_fresh(tmp_path):
    folder = tmp_path / "BTCUSD"
    folder.mkdir()
    path = folder / "2026-09-07.ndjson"
    path.write_bytes(b'{"ts_ms":')
    assert coverage_for(["BTCUSD"], tmp_path)["counts"] == {"UNREADABLE_TAIL": 1}
    path.write_text(json.dumps({"symbole": "BTCUSD", "horloge": "utc", "ts_ms": 3000}) + "\n")
    assert coverage_for(["BTCUSD"], tmp_path, now_ms=2000)["counts"] == {"FUTURE_TIMESTAMP": 1}


def test_inventory_cli_preserves_missing_symbols(tmp_path):
    result = tmp_path / "report.json"
    assert main(["--symboles", "AUDCAD", "WHEAT.fs", "--inventaire", "--quotes",
                 str(tmp_path / "quotes"), "--json", str(result)]) == 1
    assert json.loads(result.read_text())["expected_symbols"] == 2


def test_inventory_does_not_start_missing_mt5(tmp_path, monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "process_iter", lambda: iter([]))
    monkeypatch.setattr(quotes, "univers_portable", lambda: pytest.fail("must not initialize"))
    with pytest.raises(SystemExit):
        main(["--univers-mt5", "--inventaire", "--json", str(tmp_path / "report.json")])


def test_broker_ampersand_symbol_is_not_lost(tmp_path):
    result = tmp_path / "report.json"
    assert main(["--symboles", "S&P.fs", "--inventaire", "--quotes",
                 str(tmp_path / "quotes"), "--json", str(result)]) == 1
    assert json.loads(result.read_text())["assets"][0]["symbol"] == "S&P.fs"
