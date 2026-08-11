"""Pont V14 → MetaTrader 5 : export des zones pour tracé sur graphique.

POURQUOI UN FICHIER ET PAS UN APPEL DIRECT
-------------------------------------------
L'API Python de MetaTrader n'expose **aucune** fonction de dessin : ni
``ObjectCreate``, ni ``iCustom``, rien qui touche au graphique. Vérifié — sur
ses 28 fonctions publiques, zéro concerne l'affichage.

Le seul pont possible est donc le dossier ``MQL5/Files`` que le terminal lit
nativement : V14 y écrit ce qu'il voit, un indicateur compagnon
(``titanium_zones.mq5``) le relit à chaque tick et crée les objets.

CE QUI EST EXPORTÉ
------------------
Exactement ce que les portes ont regardé pour décider — pas une reconstruction
approximative faite pour l'affichage. Les niveaux tracés sur le graphique sont
**les mêmes objets** que ceux qui ont produit le verdict.

  · niveaux S/R clusterisés (avec leur force)
  · FVG **non comblées** uniquement
  · zone OTE 0.618–0.786 et son invalidation
  · VPOC et nœuds de volume
  · entrée proposée, stop, objectif — donc le R:R, visible
  · le verdict et l'état des six piliers, en clair

DEUX GARANTIES
--------------
1. **Écriture atomique** (fichier temporaire puis remplacement). Le terminal ne
   lira jamais un JSON tronqué — il lit à chaque tick, la fenêtre de collision
   serait permanente autrement.
2. **Préfixe réservé** : l'indicateur ne touche qu'aux objets nommés
   ``TITANIUM_*``. Vos tracés manuels sur le graphique restent intacts.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

#: Nom du fichier lu par l'indicateur. Doit rester synchronisé avec le .mq5.
NOM_FICHIER = "titanium_zones.json"

#: Préfixe de TOUS les objets créés par l'indicateur. Ne jamais le changer sans
#: changer aussi le .mq5 — sinon l'ancien lot d'objets ne sera plus nettoyé.
PREFIXE = "TITANIUM_"

#: Version du contrat. L'indicateur refuse un schéma qu'il ne connaît pas
#: plutôt que de dessiner n'importe quoi.
SCHEMA = 1


@dataclass
class Zone:
    """Une zone rectangulaire à tracer, en coordonnées prix."""
    kind: str                  # 'sr' | 'fvg' | 'ote' | 'vpoc'
    bas: float
    haut: float
    label: str = ""
    force: float = 0.0         # 0..1 — pilote l'opacité côté MQL5
    side: int = 0              # +1 / −1 / 0 (neutre)


@dataclass
class Plan:
    """Le plan de trade tel que la chaîne l'a produit."""
    side: int = 0
    entry: float | None = None
    sl: float | None = None
    tp: float | None = None

    @property
    def rr(self) -> float | None:
        """Rapport gain/risque, calculé — pas déclaré."""
        if None in (self.entry, self.sl, self.tp):
            return None
        risque = abs(self.entry - self.sl)
        if risque <= 0:
            return None
        return round(abs(self.tp - self.entry) / risque, 2)


@dataclass
class ZonePayload:
    """Ce que l'indicateur reçoit, pour UN symbole."""
    symbol: str
    timeframe: str
    verdict: str = ""
    code: str = ""
    pillars: list[dict] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)
    plan: Plan = field(default_factory=Plan)
    price: float | None = None
    atr: float | None = None
    bar_time: str = ""
    note: str = ""
    # ── Panel d'indicateurs, affiché en clair sur le graphique.
    #    Déclaré APRÈS `plan` à dessein : le parseur MQL5 est positionnel et
    #    ses tests verrouillent l'ordre pillars < zones < plan. Tout champ
    #    inséré avant casserait la lecture des zones en silence.
    indicators: dict = field(default_factory=dict)
    risk: dict = field(default_factory=dict)
    schema: int = SCHEMA
    written_at: str = ""

    #: Ce qu'on montre, et dans quel ordre. Une sélection plutôt que les 82
    #: familles disponibles : un panneau illisible ne sert personne, et
    #: l'ordre fixe permet de lire le graphique d'un coup d'œil.
    PANEL = ("ltf_rsi_14", "ltf_adx_14", "ltf_atr_14", "ltf_macd",
             "htf_rsi_14", "htf_adx_14", "htf_ema_50", "htf_ema_200")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["plan"]["rr"] = self.plan.rr
        d["pillars_line"] = "".join("+" if p.get("passed") else "." for p in self.pillars)
        # Une ligne prête à afficher : le parseur MQL5 sait extraire une
        # chaîne, pas parcourir un objet aux clés inconnues.
        morceaux = []
        for cle in self.PANEL:
            v = self.indicators.get(cle)
            if v is None:
                continue
            court = cle.replace("ltf_", "").replace("htf_", "H4:")
            try:
                morceaux.append(f"{court}={float(v):.4g}")
            except (TypeError, ValueError):
                continue
        d["indicators_line"] = "  ".join(morceaux)
        d["risk_line"] = (
            f"risque {self.risk.get('pct', 0):.2f}%  "
            f"lot {self.risk.get('lot', 0)}  "
            f"{self.risk.get('motif', '')}" if self.risk else "")
        return d


def dossier_mql5_files(chemin_terminal: str | Path | None = None) -> Path | None:
    """Localise le dossier ``MQL5/Files`` du terminal.

    MetaTrader range les données par terminal sous un identifiant opaque dans
    ``%APPDATA%/MetaQuotes/Terminal/<hash>/MQL5/Files``. Plusieurs dossiers
    coexistent dès qu'une seconde installation a tourné — sur cette machine,
    l'instance de test du metatester en a créé un.

    On **demande le chemin à MT5** (``terminal_info().data_path``) plutôt que
    de le déduire. L'ancienne heuristique — « le dossier le plus récemment
    modifié » — était auto-réalisatrice : y écrire notre propre fichier le
    rendait le plus récent, ce qui verrouillait le choix sur le premier venu.
    Résultat constaté le 07/08/2026 : zones et demandes de fenêtres écrites
    dans un dossier que le terminal actif ne lisait pas. Rien ne s'affichait,
    et rien ne signalait l'erreur — les fichiers étaient bien écrits.

    Le repli par date ne sert que si MT5 est injoignable.
    """
    if chemin_terminal:
        p = Path(chemin_terminal)
        return p if p.is_dir() else None

    # ── La source d'autorité : le terminal lui-même.
    try:
        import MetaTrader5 as mt5  # noqa: N813

        info = mt5.terminal_info()
        if info is not None and getattr(info, "data_path", ""):
            cible = Path(info.data_path) / "MQL5" / "Files"
            if cible.is_dir():
                return cible
    except Exception:  # noqa: BLE001 — MT5 absent : on retombe plus bas
        pass

    base = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
    if not base.is_dir():
        return None
    candidats = [d / "MQL5" / "Files" for d in base.iterdir()
                 if (d / "MQL5" / "Files").is_dir()]
    if not candidats:
        return None
    return max(candidats, key=lambda p: p.stat().st_mtime)


def zones_depuis_features(symbol: str, feats: dict, *, timeframe: str = "M15",
                          verdict: str = "", code: str = "",
                          pillars: list | None = None,
                          plan: Plan | None = None) -> ZonePayload:
    """Construit la charge utile à partir des features déjà calculées.

    On ne recalcule rien : ce qui est tracé est exactement ce qui a décidé.
    """
    trace = feats.get("_trace") or {}
    zones: list[Zone] = []

    prix = trace.get("price")
    atr = trace.get("atr") or 0.0

    # ── S/R : le niveau sur lequel le setup s'appuie. On lui donne une épaisseur
    #    d'un tiers d'ATR, sinon une ligne seule est illisible sur le graphique.
    niveau = trace.get("sr_level")
    if niveau and atr > 0:
        demi = atr / 3.0
        zones.append(Zone("sr", niveau - demi, niveau + demi,
                          label=str(trace.get("sr") or "niveau S/R"),
                          force=float(trace.get("sr_strength") or 0.5)))

    # ── FVG encore ouvertes, du côté du setup uniquement.
    for bas, haut in (trace.get("fvg_open") or []):
        zones.append(Zone("fvg", min(bas, haut), max(bas, haut),
                          label="FVG non comblée", force=0.6,
                          side=int(feats.get("liquidity") or 0)))

    # ── Golden zone OTE.
    ote = trace.get("ote_zone")
    if ote and len(ote) == 2:
        zones.append(Zone("ote", min(ote), max(ote),
                          label=str(trace.get("ote") or "OTE 0.618–0.786"),
                          force=0.7, side=int(feats.get("ote") or 0)))

    # ── VPOC : le prix le plus échangé, tracé fin.
    vpoc = trace.get("vpoc")
    if vpoc and atr > 0:
        zones.append(Zone("vpoc", vpoc - atr / 12, vpoc + atr / 12,
                          label="VPOC", force=0.4))

    return ZonePayload(
        symbol=symbol, timeframe=timeframe, verdict=verdict, code=code,
        pillars=pillars or [], zones=zones, plan=plan or Plan(),
        price=prix, atr=atr, bar_time=trace.get("bar_time", ""),
        note=str(trace.get("candle_patterns") or ""),
    )


#: Marqueur de séparation entre blocs.
#:
#: ⚠️ **Sans saut de ligne, délibérément.** L'indicateur lit le fichier avec
#: `FILE_TXT`, qui retire les fins de ligne : un séparateur contenant `\n`
#: n'est jamais retrouvé après lecture, `ExtraireBloc` rend alors le fichier
#: entier, et chaque graphique dessine les zones du PREMIER bloc. Défaut
#: constaté le 07/08/2026 — dix fenêtres, aucun tracé correct.
#:
#: Le marqueur ne peut pas apparaître dans un bloc : `json.dumps` échappe
#: tout ce qu'il contient, et aucun libellé de zone n'emploie cette forme.
MARQUEUR = "===TITANIUM==="

#: Ce qui est réellement écrit sur disque : le marqueur encadré de sauts de
#: ligne, pour rester lisible à l'œil. Côté MQL5 on cherche `MARQUEUR` seul.
SEPARATEUR = f"\n{MARQUEUR}\n"


def ecrire_multi(payloads, dossier: str | Path | None = None) -> Path | None:
    """Écrit une charge par symbole, toutes dans le même fichier.

    Pourquoi ce format
    ------------------
    L'indicateur MQL5 est instancié une fois **par graphique** et lit le même
    fichier. Avec une charge unique, la ligne ``if(sym != _Symbol) return;``
    faisait que **seul le dernier symbole écrit dessinait** — neuf fenêtres
    sur dix restaient vides par construction. Défaut constaté le 07/08/2026,
    dix graphiques ouverts et aucun tracé.

    Un seul fichier reste préférable à un fichier par symbole : l'écriture
    atomique garantit alors que tous les graphiques voient le même instant,
    et il n'y a pas de fichiers orphelins à nettoyer quand un symbole sort
    de l'univers.

    Le séparateur est une ligne entière plutôt qu'un tableau JSON : le
    parseur MQL5 cherche des sous-chaînes et ne sait pas descendre dans une
    structure imbriquée. Chaque bloc garde donc exactement le format qu'il
    sait déjà lire.
    """
    try:
        cible_dir = Path(dossier) if dossier else dossier_mql5_files()
        if cible_dir is None:
            return None
        cible_dir.mkdir(parents=True, exist_ok=True)
        cible = cible_dir / NOM_FICHIER

        maintenant = datetime.now(timezone.utc).isoformat()
        blocs = []
        for p in payloads:
            p.written_at = maintenant
            blocs.append(json.dumps(p.to_dict(), ensure_ascii=False, indent=1))

        tmp = cible.with_suffix(".tmp")
        tmp.write_text(SEPARATEUR.join(blocs), encoding="utf-8")
        tmp.replace(cible)
        return cible
    except Exception:  # noqa: BLE001 — l'affichage ne casse jamais le trading
        return None


def ecrire(payload: ZonePayload, dossier: str | Path | None = None) -> Path | None:
    """Écrit la charge utile, de façon atomique. Ne lève jamais.

    Returns:
        Le chemin écrit, ou None si le dossier du terminal est introuvable.
    """
    try:
        cible_dir = Path(dossier) if dossier else dossier_mql5_files()
        if cible_dir is None:
            return None
        cible_dir.mkdir(parents=True, exist_ok=True)
        cible = cible_dir / NOM_FICHIER

        payload.written_at = datetime.now(timezone.utc).isoformat()
        contenu = json.dumps(payload.to_dict(), ensure_ascii=False, indent=1)

        # Atomique : le terminal lit à chaque tick, un write direct exposerait
        # en permanence une fenêtre de lecture partielle.
        tmp = cible.with_suffix(".tmp")
        tmp.write_text(contenu, encoding="utf-8")
        tmp.replace(cible)
        return cible
    except Exception:  # noqa: BLE001 — l'affichage ne casse jamais le trading
        return None


def effacer(dossier: str | Path | None = None) -> bool:
    """Écrit une charge vide : l'indicateur retire ses objets du graphique."""
    return ecrire(ZonePayload(symbol="", timeframe="", verdict="", note="inactif"),
                  dossier) is not None
