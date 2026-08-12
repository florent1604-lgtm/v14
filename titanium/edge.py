"""Mesure de l'edge par contexte — et journal qui la nourrit.

Sans cette mesure, `cost.edge_ok` reste à **None** et le mode PROD bloque tout.
C'est délibéré : V12 avait un `edge_ok=True` en dur qui était un fail-OPEN
(revue Codex du 17/07/2026). Ici on ne débloque le réel que sur des chiffres.

LE CHIFFRE QUI COMPTE
---------------------
L'espérance **nette de coûts**, en R, par contexte. Pas le winrate : la leçon la
plus chère de V12 est que *winrate élevé ≠ profit*. Des configurations à 60 % de
réussite y perdaient de l'argent une fois le spread et le swap payés, et des
paramètres superbes en Python (XAUUSD PF 2.31) échouaient au testeur natif
(PF 0.90).

CE QU'EST UN CONTEXTE
---------------------
La signature d'un setup : ``(symbole, sens, famille, nombre de piliers)``. On
mesure par **classe** et non par instance — quelques centaines de trades ne
disent rien sur un couple prix/heure précis, mais disent quelque chose sur
« GER40, long, continuation, 3 piliers ».

FAIL-CLOSED SUR L'ÉCHANTILLON
------------------------------
En dessous de ``MIN_SAMPLES`` trades clos, la réponse est **None** — inconnu —
et jamais False. Confondre « je ne sais pas » et « c'est mauvais » ferait taire
des contextes jamais essayés. Le mode PROD traite les deux pareil (il bloque),
mais l'information reste distincte dans le journal et sur le dashboard.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

#: Nombre minimal de trades clos avant qu'un verdict d'edge ait un sens.
MIN_SAMPLES = 20

#: Espérance nette (en R) au-dessus de laquelle un contexte est jugé porteur.
#: Strictement positive : un contexte à espérance nulle ne paie pas le risque.
EDGE_THRESHOLD_R = 0.05

#: Borne de plausibilite absolue partagee avec la journalisation live.
#: Une valeur finie mais superieure a ce plafond reste une donnee corrompue :
#: elle ne doit ni contaminer l'esperance, ni ouvrir la porte PROD.
PNL_R_MAX = 50.0


@dataclass(frozen=True)
class Context:
    """Signature d'un setup. C'est l'unité de mesure."""
    symbol: str
    side: int
    family: str = ""
    n_pillars: int = 0

    def key(self) -> str:
        sens = "long" if self.side > 0 else "short"
        return f"{self.symbol}|{sens}|{self.family or '?'}|{self.n_pillars}p"


@dataclass
class ClosedTrade:
    """Un trade clos, tel que le journal l'enregistre."""
    context: str
    pnl_r: float                 # résultat en R, **net de coûts**
    closed_at: str = ""
    ticket: str = ""
    exit_reason: str = ""
    # Coût TOTAL aller-retour en R : spread + commission + swap + fee.
    # None signifie inconnu ; 0.0 est réservé à un coût mesuré nul.
    cost_r: float | None = None

    # ── Champs de STRATIFICATION. Tous optionnels : une ligne écrite avant
    #    leur introduction se relit sans erreur, avec ses valeurs par défaut.
    #    Sans eux, le protocole de promotion n'a rien à mesurer — il ne peut
    #    ni distinguer le live du backtest, ni isoler la strate S≥3.
    source: str = "live"         # 'live' | 'backtest'
    account: str = ""            # login du compte, pour ne pas mélanger
    mode: str = "explore"        # mode de la porte à l'ENTRÉE
    quorum: int = 0              # quorum en vigueur à l'entrée
    support_pillars: int = 0     # S ∈ 0..4, piliers ALIGNÉS (pas non nuls)
    asset_class: str = ""        # 'fx' | 'indices' | 'metaux' | 'crypto' | ...
    timeframe: str = ""          # échelle réellement analysée à l'entrée
    risk_money: float = 0.0      # risque en devise, figé à l'ouverture
    #: True ⇒ `pnl_r` vient du net en devise ET `cost_r` contient toutes
    #: les composantes mesurées. Le protocole exige 90 % de trades exacts.
    exact_cost: bool = False
    #: Qui a fourni le pilier G5 à l'entrée : "formes", "displacement" ou "".
    #: Le secours displacement (12/08/2026) fait passer G5 de 11,9 % à 23,7 % ;
    #: seul ce champ, journalisé à la CLÔTURE, permettra de comparer les deux
    #: sources sur des RÉSULTATS et non sur des taux de passage.
    candle_source: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass
class EdgeVerdict:
    """Verdict pour un contexte. ``edge_ok`` est ce que la porte consomme."""
    context: str = ""
    edge_ok: bool | None = None       # None = inconnu (échantillon insuffisant)
    samples: int = 0
    expectancy_r: float = 0.0
    win_rate: float = 0.0
    mean_cost_r: float | None = None
    known_cost_rate: float = 0.0
    exact_cost_rate: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class TradeJournal:
    """Journal append-only des trades clos (NDJSON).

    Append-only par choix : un journal qu'on peut réécrire n'est pas une preuve.
    Une ligne illisible est **sautée**, jamais fatale — un fichier corrompu ne
    doit pas empêcher de trader.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.rejected_lines = 0

    def record(self, trade: ClosedTrade) -> None:
        if not trade.closed_at:
            trade.closed_at = datetime.now(timezone.utc).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(trade.to_json() + "\n")

    def read_all(self) -> list[ClosedTrade]:
        self.rejected_lines = 0
        if not self.path.exists():
            return []
        trades: list[ClosedTrade] = []
        for ligne in self.path.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                d = json.loads(ligne)
                pnl = float(d["pnl_r"])
                if not math.isfinite(pnl) or abs(pnl) > PNL_R_MAX:
                    self.rejected_lines += 1
                    continue
                cout_exact = bool(d.get("exact_cost", False))
                brut_cout = d.get("cost_r", None)
                cout = None if brut_cout is None else float(brut_cout)
                # Ancien format : 0.0 et exact_cost=False signifiaient autant
                # "inconnu" que "gratuit". On choisit l'interpretation sure.
                if cout == 0.0 and not cout_exact:
                    cout = None
                if cout is not None and (not math.isfinite(cout) or cout < 0):
                    self.rejected_lines += 1
                    continue
                trades.append(ClosedTrade(
                    context=str(d["context"]), pnl_r=pnl,
                    closed_at=str(d.get("closed_at", "")),
                    ticket=str(d.get("ticket", "")),
                    exit_reason=str(d.get("exit_reason", "")),
                    cost_r=cout,
                    source=str(d.get("source", "live")),
                    account=str(d.get("account", "")),
                    mode=str(d.get("mode", "explore")),
                    quorum=int(d.get("quorum", 0) or 0),
                    support_pillars=int(d.get("support_pillars", 0) or 0),
                    asset_class=str(d.get("asset_class", "")),
                    timeframe=str(d.get("timeframe", "")),
                    risk_money=float(d.get("risk_money", 0.0) or 0.0),
                    exact_cost=cout_exact,
                    candle_source=str(d.get("candle_source", "") or "")))
            except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                self.rejected_lines += 1
                continue        # ligne corrompue : on la saute, on ne casse rien
        return trades


@dataclass
class EdgeBook:
    """Registre d'edge : agrège le journal et rend un verdict par contexte."""
    journal: TradeJournal
    min_samples: int = MIN_SAMPLES
    threshold_r: float = EDGE_THRESHOLD_R
    _cache: dict[str, EdgeVerdict] = field(default_factory=dict, repr=False)
    _rejected_lines: int = field(default=0, repr=False)

    def refresh(self) -> dict[str, EdgeVerdict]:
        """Relit le journal et recalcule tous les verdicts."""
        par_contexte: dict[str, list[ClosedTrade]] = defaultdict(list)
        for t in self.journal.read_all():
            par_contexte[t.context].append(t)

        self._cache = {cle: self._verdict(cle, trades)
                       for cle, trades in par_contexte.items()}
        self._rejected_lines = self.journal.rejected_lines
        if self._rejected_lines:
            for verdict in self._cache.values():
                verdict.edge_ok = False
                verdict.reason = (
                    f"journal invalide ({self._rejected_lines} ligne(s) rejetee(s))"
                )
        return self._cache

    def _verdict(self, cle: str, trades: list[ClosedTrade]) -> EdgeVerdict:
        n = len(trades)
        esperance = sum(t.pnl_r for t in trades) / n
        gagnants = sum(1 for t in trades if t.pnl_r > 0)
        couts = [float(t.cost_r) for t in trades if t.cost_r is not None]
        cout = sum(couts) / len(couts) if couts else None
        connus = len(couts) / n
        exacts = sum(1 for t in trades if t.exact_cost) / n

        v = EdgeVerdict(context=cle, samples=n,
                        expectancy_r=round(esperance, 4),
                        win_rate=round(gagnants / n, 4),
                        mean_cost_r=(round(cout, 4) if cout is not None else None),
                        known_cost_rate=round(connus, 4),
                        exact_cost_rate=round(exacts, 4))
        if n < self.min_samples:
            v.edge_ok = None
            v.reason = f"échantillon insuffisant ({n}/{self.min_samples})"
        elif esperance >= self.threshold_r:
            v.edge_ok = True
            v.reason = f"espérance nette {esperance:+.3f} R sur {n} trades"
        else:
            v.edge_ok = False
            v.reason = f"espérance nette {esperance:+.3f} R < seuil {self.threshold_r} R"
        return v

    def verdict_for(self, ctx: Context) -> EdgeVerdict:
        """Verdict d'un contexte. Inconnu tant qu'il n'a pas assez de trades."""
        if not self._cache:
            self.refresh()
        cle = ctx.key()
        if self._rejected_lines:
            return self._cache.get(cle, EdgeVerdict(
                context=cle,
                edge_ok=False,
                reason=f"journal invalide ({self._rejected_lines} ligne(s) rejetee(s))",
            ))
        return self._cache.get(cle, EdgeVerdict(
            context=cle, edge_ok=None, reason="contexte jamais mesuré"))

    def edge_ok_for(self, ctx: Context) -> bool | None:
        """Raccourci pour alimenter `build_feats(edge_ok=…)`."""
        return self.verdict_for(ctx).edge_ok

    def summary(self) -> list[EdgeVerdict]:
        """Tous les verdicts, du plus porteur au moins porteur."""
        if not self._cache:
            self.refresh()
        return sorted(self._cache.values(), key=lambda v: -v.expectancy_r)


def context_from_decision(symbol: str, decision) -> Context:
    """Contexte EXACT, construit depuis les portes.

    ⚠️ C'est la SEULE fonction qui doit produire une clé destinée au journal.

    L'ancienne construction comptait `strengths > 0`, qui mesure la
    **non-nullité** d'un pilier (`liquidity != 0`). La porte, elle, mesure
    l'**alignement** (`liquidity == side`). Un setup satisfaisant deux
    piliers, avec deux autres contre-alignés, était donc journalisé « 4p » —
    et rangé dans le seau des setups les plus forts.

    Conséquence : toute mesure d'edge par nombre de piliers était fausse, et
    c'est précisément le canal qui invaliderait une promotion en mode PROD.
    Défaut relevé le 07/08/2026, corrigé ici.

    `trend_sr` est compté en plus des quatre piliers de micro-structure : il
    est obligatoire et hors quorum, mais il fait partie de la signature du
    setup.
    """
    from titanium.gates.confluence_gate import _SUPPORT_PILLARS

    passes = {g.name for g in (getattr(decision, "gates", None) or [])
              if getattr(g, "passed", False)}
    n_piliers = len(passes & _SUPPORT_PILLARS) + (1 if "trend_sr" in passes else 0)
    return Context(
        symbol=symbol,
        side=int(getattr(decision, "side", 0) or 0),
        family=str(getattr(decision, "setup_family", "") or ""),
        n_pillars=n_piliers,
    )


def context_from_feats(symbol: str, feats: dict, side: int | None = None) -> Context:
    """Contexte depuis les features. **Délègue à la porte** pour ne pas
    entretenir un second comptage qui divergerait du premier."""
    from titanium.gates import confluence_gate

    d = confluence_gate.evaluate(feats, side=side)
    return context_from_decision(symbol, d)


#: Classes d'actifs — l'unité de promotion en mode PROD.
#:
#: Promouvoir par symbole ferait ~204 seaux testés sans correction de
#: multiplicité ; par classe il en reste quatre. C'est la différence entre
#: un critère qui mesure et un critère qui tire au sort.
ASSET_CLASSES = {
    "fx": {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
           "NZDUSD", "EURGBP", "EURJPY", "GBPJPY"},
    "indices": {"US500", "GER40", "UK100", "USTECH", "NAS100", "HSI"},
    "metaux": {"XAUUSD", "XAGUSD"},
    "crypto": {"BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD", "ADAUSD",
               "DOGUSD", "BCHUSD"},
    "energie": {"USOIL", "UKOIL"},
    "agricole": {"COCOA.FS", "COFFEE.FS", "SOYBEAN.FS"},
}


#: Groupes MT5 → classes. Le courtier range déjà ses symboles ; s'appuyer sur
#: son classement évite d'entretenir une liste qui dérive à chaque ajout.
GROUPES_MT5 = {
    "ROW_STANDARD_FX": "fx",
    "ROW_CRYPTO": "crypto",
    "ROW_STANDARD_METALS": "metaux",
    "ROW_CASH": "indices",
}

#: Le courtier range plus finement que le premier segment : `ROW_CASH\CASH_OIL`
#: n'est pas un indice, et `ROW_FUTURES` mélange indices, énergie, métaux et
#: agricole. Lire le SECOND segment évite de deviner. Constaté le 12/08/2026 :
#: `COPPER.fs` était classé « indices », c'est-à-dire retiré de la seule classe
#: — les métaux — où un avantage directionnel a été mesuré.
SOUS_GROUPES_MT5 = {
    "CASH_OIL": "energie",
}

#: `FUT_COMMODITY*` est le seul sous-groupe réellement hétérogène : on y tranche
#: sur le nom, faute de mieux, et le défaut est explicite plutôt que caché.
_ENERGIE = ("BRENT", "WTI", "OIL", "GAS", "NGAS")
_METAUX = ("COPPER", "XCU", "ALUMIN", "ZINC", "NICKEL", "PLATIN", "PALLAD",
           "XPT", "XPD", "SILVER", "GOLD")
_AGRICOLE = ("COCOA", "COFFEE", "SOYBEAN", "SUGAR", "WHEAT", "CORN", "COTTON")

#: Groupes résolus une fois par processus. Un appel MT5 par clôture de trade
#: serait payé sur le chemin critique pour une information immuable.
_CACHE_GROUPES: dict = {}


def classe_depuis_groupe(groupe: str, symbole: str = "") -> str:
    """Classe déduite du chemin MT5. Rend "" si le groupe est inconnu.

    Le chemin complet est utilisé (``ROW_CASH\\CASH_OIL\\UKOIL``), pas seulement
    sa racine : c'est la classification du courtier, pas une devinette.
    """
    chemin = [p.strip().upper() for p in str(groupe or "").split("\\") if p.strip()]
    if not chemin:
        return ""
    racine = chemin[0]
    sous = chemin[1] if len(chemin) > 1 else ""

    if sous in SOUS_GROUPES_MT5:
        return SOUS_GROUPES_MT5[sous]
    if sous.startswith("CASH_INDICES") or sous.startswith("FUT_INDICES"):
        return "indices"
    if sous.startswith("FUT_COMMODITY") or racine == "ROW_FUTURES":
        s = str(symbole or "").upper()
        if any(e in s for e in _ENERGIE):
            return "energie"
        if any(m in s for m in _METAUX):
            return "metaux"
        if any(a in s for a in _AGRICOLE):
            return "agricole"
        return "indices"
    return GROUPES_MT5.get(racine, "")


def _groupe_mt5(symbole: str) -> str:
    """Groupe du symbole selon MT5, mis en cache. Ne lève jamais.

    ⚠️ Le catalogue n'est lisible que dans une SESSION initialisée. Appeler
    ``symbols_get()`` sur un module MetaTrader5 simplement importé renvoie
    ``None`` sans erreur : la classe devient "" et l'actif cesse d'être
    promouvable. Le défaut ne se voyait pas depuis la boucle de trading, qui
    initialise MT5 pour d'autres raisons, mais faussait tout outil d'analyse
    lancé séparément — le 12/08/2026, 66 % des candidats ressortaient
    « inconnue » dans une mesure d'exposition par classe.
    """
    s = str(symbole or "").upper()
    if s in _CACHE_GROUPES:
        return _CACHE_GROUPES[s]
    try:
        from titanium.data.mt5_vendor import mt5_session

        with mt5_session() as mt5:
            for spec in (mt5.symbols_get() or []):
                _CACHE_GROUPES[spec.name.upper()] = spec.path or ""
    except Exception:  # noqa: BLE001 — hors ligne : on garde la liste en dur
        pass
    return _CACHE_GROUPES.get(s, "")


def asset_class_of(symbol: str) -> str:
    """Classe d'un symbole, ou "" s'il reste inclassable.

    Deux sources, dans cet ordre : la liste explicite ci-dessus, puis le
    groupe déclaré par MT5. La liste passe en premier pour que les tests
    restent déterministes hors ligne ; MT5 couvre le reste du catalogue.

    ⚠️ Une classe vide n'est **jamais** promouvable. Ce n'est pas un détail :
    124 des 149 symboles du catalogue étaient inclassables avant cette
    résolution, et leurs trades n'auraient jamais compté pour l'ouverture du
    mode PROD — on aurait accumulé sans jamais avancer.
    """
    s = str(symbol or "").upper()
    for cls, syms in ASSET_CLASSES.items():
        if s in syms:
            return cls
    return classe_depuis_groupe(_groupe_mt5(s), s)
