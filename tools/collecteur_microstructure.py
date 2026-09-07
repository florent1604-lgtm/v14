"""Collecteur public et leger de microstructure crypto multi-place.

Les endpoints lus sont publics et ne permettent pas d'envoyer des ordres.
Le collecteur ne connait ni MT5, ni compte, ni cle API. Il remplace des appels
reseau dans la boucle de trading par un petit instantane JSON atomique.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.microstructure import FRESHNESS_MS, VenueSnapshot, aggregate_snapshots  # noqa: E402

SYMBOLS = ("BTCUSDT", "ETHUSDT")
INTERVAL_S = 5.0
DEPTH = 25
TRADE_LIMIT = 100
OUTPUT = RACINE / "results" / "microstructure"
_stop = False


def _levels(rows) -> tuple[tuple[float, float], ...]:
    return tuple((float(row[0]), float(row[1])) for row in rows
                 if len(row) >= 2 and float(row[0]) > 0 and float(row[1]) > 0)


def _trades(rows, *, price: str, quantity: str, side: str, timestamp: str,
            side_parser=lambda value: str(value).lower()):
    return tuple(
        (float(row[timestamp]), side_parser(row[side]),
         float(row[quantity]), float(row[price]))
        for row in rows
        if float(row.get(quantity, 0.0) or 0.0) > 0.0
        and float(row.get(price, 0.0) or 0.0) > 0.0
    )


def parse_binance(symbol: str, book: dict, trades: list, *,
                  received_ms: float) -> VenueSnapshot:
    normalized_trades = tuple(
        (float(row["T"]), "sell" if bool(row.get("m")) else "buy",
         float(row["q"]), float(row["p"]))
        for row in trades
        if float(row.get("q", 0.0) or 0.0) > 0.0
    )
    event_ms = max((row[0] for row in normalized_trades), default=received_ms)
    return VenueSnapshot("binance", symbol, _levels(book["bids"]),
                         _levels(book["asks"]), normalized_trades,
                         event_ms, received_ms)


def parse_bybit(symbol: str, book: dict, trades: dict, *,
                received_ms: float) -> VenueSnapshot:
    if int(book.get("retCode", -1)) != 0 or int(trades.get("retCode", -1)) != 0:
        raise ValueError("reponse Bybit en echec")
    b = book["result"]
    rows = trades["result"]["list"]
    normalized = _trades(rows, price="price", quantity="size", side="side",
                         timestamp="time")
    book_ms = float(b["ts"])
    event_ms = min(book_ms, max((row[0] for row in normalized), default=book_ms))
    return VenueSnapshot("bybit", symbol, _levels(b["b"]), _levels(b["a"]),
                         normalized, event_ms, received_ms)


def parse_okx(symbol: str, book: dict, trades: dict, *,
              received_ms: float) -> VenueSnapshot:
    if str(book.get("code")) != "0" or str(trades.get("code")) != "0":
        raise ValueError("reponse OKX en echec")
    b = book["data"][0]
    normalized = _trades(trades["data"], price="px", quantity="sz", side="side",
                         timestamp="ts")
    book_ms = float(b["ts"])
    event_ms = min(book_ms, max((row[0] for row in normalized), default=book_ms))
    return VenueSnapshot("okx", symbol, _levels(b["bids"]), _levels(b["asks"]),
                         normalized, event_ms, received_ms)


def _get_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "V14-market-data/1"})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json_timed(url: str) -> tuple[Any, float]:
    data = _get_json(url)
    return data, time.time() * 1000.0


def _urls(symbol: str) -> dict[str, tuple[str, str]]:
    compact = symbol.upper()
    dashed = f"{compact[:-4]}-USDT"
    quoted = urllib.parse.quote
    return {
        "binance": (
            f"https://data-api.binance.vision/api/v3/depth?symbol={quoted(compact)}&limit={DEPTH}",
            f"https://data-api.binance.vision/api/v3/aggTrades?symbol={quoted(compact)}&limit={TRADE_LIMIT}",
        ),
        "bybit": (
            f"https://api.bybit.com/v5/market/orderbook?category=spot&symbol={quoted(compact)}&limit={DEPTH}",
            f"https://api.bybit.com/v5/market/recent-trade?category=spot&symbol={quoted(compact)}&limit={TRADE_LIMIT}",
        ),
        "okx": (
            f"https://www.okx.com/api/v5/market/books?instId={quoted(dashed)}&sz={DEPTH}",
            f"https://www.okx.com/api/v5/market/trades?instId={quoted(dashed)}&limit={TRADE_LIMIT}",
        ),
    }


def collect_symbol(symbol: str) -> tuple[dict, dict[str, str]]:
    urls = _urls(symbol)
    fetched: dict[tuple[str, str], Any] = {}
    received: dict[tuple[str, str], float] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(_get_json_timed, url): (venue, kind)
            for venue, pair in urls.items()
            for kind, url in zip(("book", "trades"), pair, strict=True)
        }
        for future in as_completed(futures):
            venue, kind = futures[future]
            try:
                fetched[(venue, kind)], received[(venue, kind)] = future.result()
            except Exception as exc:  # noqa: BLE001 - une place peut tomber seule
                errors[venue] = type(exc).__name__
    now_ms = time.time() * 1000.0
    venues = []
    parsers = {"binance": parse_binance, "bybit": parse_bybit, "okx": parse_okx}
    for venue, parser in parsers.items():
        try:
            venues.append(parser(symbol, fetched[(venue, "book")],
                                 fetched[(venue, "trades")], received_ms=min(
                                     received[(venue, "book")], received[(venue, "trades")],
                                 )))
        except Exception as exc:  # noqa: BLE001 - degrade sans inventer
            errors[venue] = type(exc).__name__
    snapshot = aggregate_snapshots(venues, now_ms=now_ms)
    snapshot["errors"] = errors
    return snapshot, errors


def write_atomic(snapshot: dict, output: Path = OUTPUT) -> Path:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"{snapshot['symbol']}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                         encoding="utf-8")
    temporary.replace(target)
    return target


def cycle(symbols=SYMBOLS, *, output: Path = OUTPUT) -> int:
    """Publish each completed asset without waiting for a slower neighbour.

    Two assets at most, each with six bounded HTTP workers: this limit stays
    constant even when the configured universe grows. Only this thread writes.
    """
    written = 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(collect_symbol, symbol): symbol
                   for symbol in dict.fromkeys(symbols)}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                snapshot, errors = future.result()
                write_atomic(snapshot, output)
                written += 1
                print(
                    f"  {symbol}: {snapshot['venue_count']} places, "
                    f"depth={snapshot['depth_imbalance_10bps']:+.2f}, "
                    f"flow={snapshot['taker_imbalance_recent']:+.2f}"
                    + (f" erreurs={errors}" if errors else ""),
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  {symbol}: indisponible ({type(exc).__name__})", flush=True)
    return written


def _stopper(*_args) -> None:
    global _stop
    _stop = True


@contextmanager
def collector_lock(output: Path):
    """OS-held single writer lock; crashes release it without deleting evidence."""
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".collector.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def collection_health(symbols, output: Path, *, now_ms=None):
    now = time.time() * 1000.0 if now_ms is None else now_ms
    assets = {}
    for symbol in symbols:
        try:
            row = json.loads((output / f"{symbol}.json").read_text(encoding="utf-8"))
            age = max(now - float(row["received_ms"]), now - float(row["event_ms"]))
            healthy = (math.isfinite(age) and -1000. <= age <= FRESHNESS_MS
                       and row["symbol"] == symbol and row["fresh"]
                       and row["venue_count"] >= 2)
            assets[symbol] = {"status": "OK" if healthy else "WAIT",
                              "age_ms": age if math.isfinite(age) else None,
                              "venues": row["venue_count"]}
        except (OSError, KeyError, TypeError, ValueError):
            assets[symbol] = {"status": "WAIT", "reason": "MISSING_OR_INVALID"}
    return {"symbol": "_health", "schema": "v14.collector-health.v1", "observed_ms": now,
            "pid": os.getpid(), "assets": assets,
            "status": "OK" if assets and all(a["status"] == "OK" for a in assets.values()) else "WAIT"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symboles", nargs="*", default=list(SYMBOLS))
    parser.add_argument("--intervalle", type=float, default=INTERVAL_S)
    parser.add_argument("--une-fois", action="store_true")
    parser.add_argument("--dossier", default=str(OUTPUT))
    args = parser.parse_args(argv)
    if not math.isfinite(args.intervalle) or args.intervalle < 1.0:
        parser.error("intervalle fini d'au moins une seconde requis")
    signal.signal(signal.SIGINT, _stopper)
    print("Microstructure publique Binance + Bybit + OKX (aucune cle, aucun ordre).",
          flush=True)
    symbols = tuple(s.upper() for s in args.symboles)
    if not symbols or any(not s.isalnum() for s in symbols):
        parser.error("symboles alphanumeriques explicites requis")
    output = Path(args.dossier)
    try:
        with collector_lock(output):
            while not _stop:
                deadline = time.monotonic() + args.intervalle
                cycle(symbols, output=output)
                health = collection_health(symbols, output)
                write_atomic(health, output)
                if args.une_fois:
                    return 0 if health["status"] == "OK" else 1
                while not _stop and time.monotonic() < deadline:
                    time.sleep(max(0.0, min(0.25, deadline - time.monotonic())))
    except OSError as exc:
        print(f"Collecteur arrete, verrou ou stockage indisponible ({type(exc).__name__}).",
              flush=True)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
