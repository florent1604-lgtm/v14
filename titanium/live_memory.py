"""Memoire d'edge live construite depuis les rejeux V4 scelles.

La memoire ne cree jamais de signal et ne modifie jamais un ordre. Elle rend
uniquement un verdict d'admission pour une entree deja validee par Titanium.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class MemoryVerdict:
    action: str
    reason: str
    samples: int = 0
    expectancy_r: float = 0.0
    profit_factor: float = 0.0
    source: str = "replay_v4"

    def to_dict(self) -> dict:
        return asdict(self)


def _canonical(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _sealed(root: Path, symbol: str) -> tuple[Path, Path] | None:
    raw = root / "results" / "rejeu_univers_brut" / symbol / "trades.ndjson"
    manifest_path = raw.parent / "manifest.json"
    summary = root / "results" / "rejeu_univers" / f"{symbol}.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        seal = manifest.pop("manifest_sha256")
        if seal != hashlib.sha256(_canonical(manifest)).hexdigest():
            return None
        if (manifest.get("schema_version") != 2
                or manifest.get("symbol") != symbol
                or manifest.get("artifact_type") != "v14.offline_replay.trades"):
            return None
        trade_info = manifest["trades"]
        raw_bytes = raw.read_bytes()
        if (len(raw_bytes) != trade_info["bytes"]
                or hashlib.sha256(raw_bytes).hexdigest() != trade_info["sha256"]):
            return None
        summary_info = manifest.get("summary")
        summary_bytes = summary.read_bytes()
        if (not summary_info or summary_info.get("name") != summary.name
                or len(summary_bytes) != summary_info.get("bytes")
                or hashlib.sha256(summary_bytes).hexdigest()
                != summary_info.get("sha256")):
            return None
        return raw, summary
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _stats(values: list[float]) -> tuple[int, float, float]:
    n = len(values)
    if not n:
        return 0, 0.0, 0.0
    gains = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    pf = gains / losses if losses else (99.0 if gains else 0.0)
    return n, sum(values) / n, pf


class ReplayEdgeMemory:
    """Charge a la demande un actif, puis conserve son verdict en memoire."""

    def __init__(self, root: Path, *, min_context: int = 60,
                 min_symbol: int = 100, threshold_r: float = 0.05):
        self.root = Path(root)
        self.min_context = min_context
        self.min_symbol = min_symbol
        self.threshold_r = threshold_r
        self._cache: dict[str, dict] = {}

    def _load(self, symbol: str) -> dict | None:
        if symbol in self._cache:
            return self._cache[symbol]
        paths = _sealed(self.root, symbol)
        if paths is None:
            return None
        raw, summary_path = paths
        contexts: dict[str, list[float]] = {}
        all_values: list[float] = []
        try:
            for line in raw.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row.get("split") != "verification":
                    continue
                value = float(row["net_r"])
                contexts.setdefault(str(row["context"]), []).append(value)
                all_values.append(value)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
        data = {"contexts": contexts, "all": all_values, "summary": summary}
        self._cache[symbol] = data
        return data

    def verdict(self, symbol: str, context: str) -> MemoryVerdict:
        data = self._load(symbol)
        if data is None:
            return MemoryVerdict("WAIT", "artefact V4 absent ou sceau invalide")
        exact = list(data["contexts"].get(context, ()))
        values = exact if len(exact) >= self.min_context else list(data["all"])
        minimum = self.min_context if values is exact else self.min_symbol
        n, expectancy, pf = _stats(values)
        scope = "contexte exact" if values is exact else "actif (repli verifie)"
        # Adaptation live conservative : trois pertes consecutives sur le
        # contexte suspendent les nouvelles entrees. Les SL existants restent
        # strictement intacts; seule l'admission suivante est concernee.
        #
        # Mais la serie perdante ne PRIME PAS sur une esperance mesuree.
        # `_recent_live` n'a aucune fenetre de recence : il prend les vingt
        # dernieres clotures du contexte, quelle que soit leur date. Sans la
        # subordination ci-dessous, trois pertes vieilles de douze jours
        # suspendaient indefiniment un contexte que des centaines de clotures
        # donnent gagnant. Mesure du 08/09/2026 : 24 contextes suspendus, dont
        # 21 a esperance positive — jusqu'a E=+0.199 R et PF 1.53 sur 407
        # clotures. La regle inconditionnelle eteignait en priorite les
        # contextes les plus rentables, ce qui est l'inverse de son but.
        # Elle continue de bloquer partout ou l'esperance ne la contredit pas :
        # edge negatif, ou echantillon trop court pour en juger.
        recent = self._recent_live(context)
        serie_perdante = (len(recent) >= 3
                          and all(value <= 0 for value in recent[-3:]))
        edge_positif = (n >= minimum and expectancy > self.threshold_r
                        and pf > 1.0)
        if serie_perdante and not edge_positif:
            return MemoryVerdict("BLOCK", "3 pertes live consecutives",
                                 len(recent), round(sum(recent) / len(recent), 4),
                                 round(_stats(recent)[2], 4), "live_recent")
        if n < minimum:
            return MemoryVerdict("WAIT", f"{scope}: echantillon {n}/{minimum}",
                                 n, round(expectancy, 4), round(pf, 4))
        if expectancy <= self.threshold_r or pf <= 1.0:
            return MemoryVerdict("BLOCK", f"{scope}: edge insuffisant",
                                 n, round(expectancy, 4), round(pf, 4))
        return MemoryVerdict("ALLOW", f"{scope}: edge V4 positif",
                             n, round(expectancy, 4), round(pf, 4))

    def _recent_live(self, context: str, limit: int = 20) -> list[float]:
        path = self.root / "results" / "trades.ndjson"
        if not path.exists():
            return []
        values: list[float] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row.get("context") == context and row.get("source", "live") == "live":
                    values.append(float(row["pnl_r"]))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return []
        return values[-limit:]

    def record(self, symbol: str, context: str, verdict: MemoryVerdict) -> None:
        path = self.root / "results" / "live_memory.ndjson"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            row = {"at": datetime.now(timezone.utc).isoformat(),
                   "symbol": symbol, "context": context, **verdict.to_dict()}
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass
