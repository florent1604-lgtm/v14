"""Organe Market-JEPA leger pour le chemin cognitif V14.

Le modele predit uniquement deux etats latents (volatilite et impulsion). La
direction est volontairement absente: les simulations V14 n'ont pas demontre
d'edge directionnel. Cet organe informe le cortex; il ne bloque ni n'execute.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np

SCHEMA_VERSION = 1
DEFAULT_CONTEXT = 64
DEFAULT_BLOCKS = 8
PREDICTION_NAMES = ("vol_ratio", "impulse_r")


def context_vector(frame, *, context: int = DEFAULT_CONTEXT,
                   blocks: int = DEFAULT_BLOCKS) -> np.ndarray:
    """Encode un contexte OHLCV en representation compacte et stable."""
    if frame is None or len(frame) < context + 1 or context % blocks:
        raise ValueError("contexte Market-JEPA insuffisant")
    recent = frame.iloc[-(context + 1):]
    close = np.asarray(recent["close"], dtype=float)
    high = np.asarray(recent["high"], dtype=float)[1:]
    low = np.asarray(recent["low"], dtype=float)[1:]
    volume = np.asarray(recent.get("tick_volume", np.ones(len(recent))), dtype=float)[1:]
    if (not np.isfinite(close).all() or (close <= 0).any()
            or not np.isfinite(high).all() or not np.isfinite(low).all()):
        raise ValueError("barres Market-JEPA invalides")
    returns = np.diff(np.log(close))
    ranges = np.maximum(0.0, high - low) / close[1:]
    log_volume = np.log1p(np.maximum(0.0, volume))
    vol_scale = float(np.std(log_volume)) or 1.0
    log_volume = (log_volume - float(np.mean(log_volume))) / vol_scale
    width = context // blocks
    encoded: list[float] = []
    for start in range(0, context, width):
        sl = slice(start, start + width)
        r = returns[sl]
        encoded.extend((
            float(np.mean(r)),
            float(np.std(r)),
            float(np.mean(np.abs(r))),
            float(np.mean(ranges[sl])),
            float(np.mean(log_volume[sl])),
        ))
    vector = np.asarray(encoded, dtype=float)
    if not np.isfinite(vector).all():
        raise ValueError("representation Market-JEPA invalide")
    return vector


def training_examples(frame, *, context: int = DEFAULT_CONTEXT,
                      horizon: int = 4, blocks: int = DEFAULT_BLOCKS,
                      stride: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """Construit des paires contexte/cible sans fuite temporelle."""
    rows_x: list[np.ndarray] = []
    rows_y: list[tuple[float, float]] = []
    for end in range(context + 1, len(frame) - horizon, max(1, stride)):
        history = frame.iloc[end - context - 1:end]
        future = frame.iloc[end - 1:end + horizon]
        try:
            vector = context_vector(history, context=context, blocks=blocks)
            fclose = np.asarray(future["close"], dtype=float)
            future_returns = np.diff(np.log(fclose))
            history_close = np.asarray(history["close"], dtype=float)
            history_returns = np.diff(np.log(history_close))
            base_vol = float(np.std(history_returns)) or 1e-12
            base_range = float(np.mean(
                (np.asarray(history["high"], dtype=float)
                 - np.asarray(history["low"], dtype=float))
                / np.asarray(history["close"], dtype=float)
            )) or 1e-12
            vol_ratio = float(np.std(future_returns) / base_vol)
            impulse = float(np.max(np.abs(np.cumsum(future_returns))) / base_range)
            if math.isfinite(vol_ratio) and math.isfinite(impulse):
                rows_x.append(vector)
                rows_y.append((min(vol_ratio, 10.0), min(impulse, 10.0)))
        except (KeyError, TypeError, ValueError, FloatingPointError):
            continue
    if not rows_x:
        return np.empty((0, blocks * 5)), np.empty((0, 2))
    return np.vstack(rows_x), np.asarray(rows_y, dtype=float)


def fit_ridge(x: np.ndarray, y: np.ndarray, *, alpha: float = 2.0) -> dict:
    """Ajuste un predicteur latent deterministe exportable sans pickle."""
    if x.ndim != 2 or y.ndim != 2 or len(x) != len(y) or len(x) < 20:
        raise ValueError("echantillon Market-JEPA insuffisant")
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-12] = 1.0
    z = (x - mean) / scale
    design = np.column_stack((np.ones(len(z)), z))
    penalty = np.eye(design.shape[1]) * float(alpha)
    penalty[0, 0] = 0.0
    coef = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    return {
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "intercept": coef[0].tolist(),
        "weights": coef[1:].T.tolist(),
        "samples": int(len(x)),
    }


class MarketJepaRuntime:
    """Charge un artefact scelle et predit en memoire en quelques ms."""

    def __init__(self, model_path: Path):
        self.model_path = Path(model_path)
        self.manifest_path = self.model_path.with_suffix(".manifest.json")
        self._stamp = (-1, -1)
        self._model: dict[str, Any] | None = None
        self._error = "MODEL_MISSING"

    def _load(self) -> None:
        try:
            stamp = (
                self.model_path.stat().st_mtime_ns,
                self.manifest_path.stat().st_mtime_ns,
            )
            if self._model is not None and stamp == self._stamp:
                return
            raw = self.model_path.read_bytes()
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if hashlib.sha256(raw).hexdigest() != manifest.get("sha256"):
                raise ValueError("empreinte Market-JEPA invalide")
            model = json.loads(raw)
            if int(model.get("schema_version", 0)) != SCHEMA_VERSION:
                raise ValueError("schema Market-JEPA incompatible")
            self._model = model
            self._stamp = stamp
            self._error = ""
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._model = None
            self._error = type(exc).__name__.upper()

    @staticmethod
    def _predict(vector: np.ndarray, head: dict) -> np.ndarray:
        mean = np.asarray(head["mean"], dtype=float)
        scale = np.asarray(head["scale"], dtype=float)
        weights = np.asarray(head["weights"], dtype=float)
        intercept = np.asarray(head["intercept"], dtype=float)
        return intercept + weights @ ((vector - mean) / scale)

    def read(self, symbol: str, frame) -> dict[str, float]:
        started = time.perf_counter()
        self._load()
        if self._model is None:
            return {"jepa_available": 0.0, "jepa_latency_ms": round(
                (time.perf_counter() - started) * 1000, 3)}
        try:
            context = int(self._model["context"])
            blocks = int(self._model["blocks"])
            vector = context_vector(frame, context=context, blocks=blocks)
            head = self._model.get("assets", {}).get(symbol.upper())
            head = head or self._model["global"]
            predicted = np.clip(self._predict(vector, head), 0.0, 10.0)
            return {
                "jepa_available": 1.0,
                "jepa_vol_ratio": round(float(predicted[0]), 6),
                "jepa_impulse_r": round(float(predicted[1]), 6),
                "jepa_latency_ms": round(
                    (time.perf_counter() - started) * 1000, 3),
            }
        except (KeyError, TypeError, ValueError, FloatingPointError):
            return {"jepa_available": 0.0, "jepa_latency_ms": round(
                (time.perf_counter() - started) * 1000, 3)}


def attach_market_jepa(symbol: str, feats: dict, frame,
                       runtime: MarketJepaRuntime) -> dict[str, float]:
    reading = runtime.read(symbol, frame)
    trace = feats.setdefault("_trace", {})
    indicators = trace.setdefault("indicators", {})
    indicators.update(reading)
    return reading
