"""Pilotage du testeur de stratégies MT5 depuis V14.

Pourquoi ce pont existe
-----------------------
Le rejeu Python (`titanium/backtest.py`) est rapide et instrumenté, mais il
simule : entrée à l'ouverture de barre, coût forfaitaire, pas de slippage,
pas de tick intra-barre. V12 a payé cet écart au prix fort — XAUUSD donnait
PF 2.31 en Python et PF 0.90 au testeur natif, *même stratégie*. Le testeur
MT5 rejoue sur ticks réels avec le vrai carnet de coûts du courtier.

Le second intérêt est le coût : un balayage de plusieurs milliers de passes
ne consomme aucun jeton. Le raisonnement est réservé à la lecture des
résultats.

Ce que ce pont N'EST PAS
------------------------
Il ne porte pas la chaîne de décision en MQL5. `titanium_replay.mq5` rejoue
des entrées décidées par V14 et n'optimise que la *gestion*. L'optimiseur
répond donc à « comment mieux gérer ces entrées », jamais à « faut-il les
prendre ». Toute conclusion sur la qualité des entrées reste hors de sa
portée — et ce module ne prétendra jamais le contraire.
"""

from __future__ import annotations

import csv
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ── Le fichier de signaux porte un nom fixe : `#property tester_file` de
#    MQL5 exige un littéral de compilation. Un seul fichier contient donc
#    tous les symboles, et l'EA filtre sur le sien.
NOM_SIGNAUX = "titanium_signals.csv"
NOM_EA = "titanium_replay"

# Critère d'optimisation 6 = « Custom max », c'est-à-dire la valeur rendue
# par `OnTester()`. On veut l'espérance en R, pas le profit : maximiser le
# profit récompense les grosses tailles, pas les bons réglages.
CRITERE_CUSTOM_MAX = 6

# Modèle 4 = ticks réels. Plus lent que l'OHLC minute, mais c'est
# exactement l'écart que ce pont existe pour mesurer.
MODELE_TICKS_REELS = 4
MODELE_OHLC_M1 = 1

# Le mode « forward » de MT5 réserve une fraction finale de la période et
# rejoue dessus les meilleurs réglages. C'est un walk-forward intégré, et
# c'est le seul garde-fou sérieux contre le sur-ajustement d'un balayage.
FORWARD_UN_TIERS = 2


@dataclass(frozen=True)
class Plage:
    """Un paramètre à balayer.

    `pas=0` fige la valeur : elle est transmise à l'EA sans être optimisée.
    """

    nom: str
    depart: float
    pas: float = 0.0
    fin: float = 0.0

    def rendu_ini(self) -> str:
        """Format attendu par MT5 : `valeur||début||pas||fin||actif`."""
        if self.pas <= 0.0:
            return f"{self.nom}={_num(self.depart)}"
        return (
            f"{self.nom}={_num(self.depart)}"
            f"||{_num(self.depart)}||{_num(self.pas)}||{_num(self.fin)}||Y"
        )

    def cardinal(self) -> int:
        """Nombre de valeurs testées — sert au décompte des combinaisons."""
        if self.pas <= 0.0:
            return 1
        return int(round((self.fin - self.depart) / self.pas)) + 1


def _num(v: float) -> str:
    """MT5 lit mal les flottants en notation scientifique ou surchargés."""
    if float(v) == int(v):
        return str(int(v))
    return f"{v:.6f}".rstrip("0").rstrip(".")


@dataclass
class Passe:
    """Une combinaison évaluée par l'optimiseur."""

    params: dict[str, float] = field(default_factory=dict)
    resultat: float = 0.0          # profit net, devise du compte
    trades: int = 0
    profit_factor: float = 0.0
    esperance_r: float = 0.0       # ce que rend `OnTester()`
    drawdown: float = 0.0
    sharpe: float = 0.0
    forward: bool = False          # passe hors échantillon ?

    def viable(self, *, min_trades: int = 30, min_pf: float = 1.2) -> bool:
        """Critères de V12, repris tels quels.

        Ils sont volontairement sévères : c'est le protocole qui a survécu
        à l'échec de V12, pas celui qui l'a causé.
        """
        return (
            self.trades >= min_trades
            and self.profit_factor >= min_pf
            and self.esperance_r > 0.0
        )


# ────────────────────────────────────────────────────────────────────────
# Export des signaux
# ────────────────────────────────────────────────────────────────────────

ENTETE = ["symbol", "epoch", "side", "entry", "sl", "tp",
          "r_unit", "contexte", "pillars", "family"]


def exporter_signaux(trades, chemin: Path) -> int:
    """Écrit les décisions de V14 dans le format lu par l'EA.

    `trades` : itérable de `titanium.backtest.Trade`.

    Les signaux sont triés par horodatage — l'EA avance un curseur sans
    jamais revenir en arrière, ce qui suppose l'ordre chronologique.
    """
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)

    lignes = []
    for t in trades:
        epoch = _epoch(t.bar_entree)
        if epoch is None or t.r_unit <= 0 or t.side not in (1, -1):
            continue
        lignes.append([
            _ascii(t.symbol), epoch, t.side,
            f"{t.prix_entree:.8f}", f"{t.sl:.8f}", f"{t.tp:.8f}",
            f"{t.r_unit:.8f}", _ascii(t.contexte), t.pillars,
            _ascii(t.family),
        ])

    lignes.sort(key=lambda r: (r[0], r[1]))

    # `newline=""` : sans quoi Windows insère un \r surnuméraire que le
    # lecteur CSV de MQL5 rattache au dernier champ de chaque ligne.
    with chemin.open("w", encoding="ascii", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\n")
        w.writerow(ENTETE)
        w.writerows(lignes)

    return len(lignes)


def _ascii(txt) -> str:
    """Réduit un champ texte à l'ASCII, sans jamais lever.

    L'EA lit en `FILE_ANSI` : un accent y arriverait corrompu, et le
    point-virgule est le séparateur, donc interdit dans un champ. Mieux
    vaut un libellé translittéré qu'un export qui s'interrompt — ces
    colonnes ne servent qu'à la traçabilité, pas à l'exécution.
    """
    import unicodedata

    plat = unicodedata.normalize("NFKD", str(txt))
    plat = plat.encode("ascii", "ignore").decode("ascii")
    return plat.replace(";", ",").replace("\n", " ").strip()


def _epoch(bar: str) -> int | None:
    """Horodatage ISO → epoch UTC, tolérant aux formats rencontrés."""
    if not bar:
        return None
    txt = str(bar).strip().replace("T", " ")
    txt = re.sub(r"[+-]\d{2}:?\d{2}$", "", txt).replace("Z", "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(txt, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


# ────────────────────────────────────────────────────────────────────────
# Configuration du testeur
# ────────────────────────────────────────────────────────────────────────

def ecrire_ini(chemin: Path, *, symbol: str, periode: str,
               depuis: str, jusqua: str, plages: list[Plage],
               rapport: str, depot: float = 10_000.0,
               devise: str = "USD", levier: int = 100,
               modele: int = MODELE_TICKS_REELS,
               forward: int = FORWARD_UN_TIERS,
               optimisation: int = 1,
               compte: tuple[str, str, str] | None = None) -> Path:
    """Produit le `.ini` que `terminal64.exe /config:` consomme.

    `optimisation` : 1 = balayage complet, 0 = passe unique, 2 = génétique.
    Le génétique est tentant sur les grandes grilles, mais il explore de
    façon non reproductible — à proscrire tant qu'on cherche à *mesurer*.
    """
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)

    corps = []

    # ── Une instance neuve n'a aucun compte : le testeur refuse alors de
    #    démarrer (« tester not started because the account is not
    #    specified ») et le terminal se ferme sans rapport ni explication.
    #    Le compte sert à obtenir les spécifications de symbole et à
    #    télécharger l'historique — même pour un simple rejeu.
    if compte:
        login, motdepasse, serveur = compte
        corps += [
            "[Common]",
            f"Login={login}",
            f"Password={motdepasse}",
            f"Server={serveur}",
            "",
        ]

    corps += [
        "[Tester]",
        f"Expert={NOM_EA}",
        f"Symbol={symbol}",
        f"Period={periode}",
        f"Model={modele}",
        f"Optimization={optimisation}",
        f"OptimizationCriterion={CRITERE_CUSTOM_MAX}",
        f"FromDate={depuis}",
        f"ToDate={jusqua}",
        f"ForwardMode={forward}",
        f"Deposit={_num(depot)}",
        f"Currency={devise}",
        f"Leverage=1:{levier}",
        "ExecutionMode=0",
        f"Report={rapport}",
        "ReplaceReport=1",
        "ShutdownTerminal=1",
        "Visual=0",
        "",
        "[TesterInputs]",
    ]
    corps += [p.rendu_ini() for p in plages]

    chemin.write_text("\n".join(corps) + "\n", encoding="utf-16")
    return chemin


def combinaisons(plages: list[Plage]) -> int:
    """Produit des cardinaux — le chiffre qui fixe la sévérité du contrôle.

    Il doit figurer dans tout rapport : un « meilleur réglage » tiré de
    4 000 passes n'a pas le même statut qu'un tiré de 12.
    """
    n = 1
    for p in plages:
        n *= p.cardinal()
    return n


# ────────────────────────────────────────────────────────────────────────
# Exécution
# ────────────────────────────────────────────────────────────────────────

def lancer(terminal: Path, ini: Path, *, timeout: int = 7200,
           portable: bool = False) -> int:
    """Lance le testeur et attend la fin.

    `ShutdownTerminal=1` dans le `.ini` fait que le terminal se ferme de
    lui-même ; sans cela l'appel ne rendrait jamais la main.

    `portable` fait vivre les données dans le dossier du programme. C'est
    ce qui permet à une seconde instance de tourner sans toucher au
    terminal qui trade — MT5 refusant deux instances sur le même dossier
    de données.
    """
    terminal, ini = Path(terminal), Path(ini)
    if not terminal.exists():
        raise FileNotFoundError(f"terminal MT5 introuvable : {terminal}")
    if not ini.exists():
        raise FileNotFoundError(f"configuration introuvable : {ini}")

    cmd = [str(terminal)]
    if portable:
        cmd.append("/portable")
    cmd.append(f"/config:{ini}")

    proc = subprocess.run(
        cmd, capture_output=True, timeout=timeout, check=False,
    )
    return proc.returncode


# ────────────────────────────────────────────────────────────────────────
# Lecture du rapport
# ────────────────────────────────────────────────────────────────────────

_NS = "{urn:schemas-microsoft-com:office:spreadsheet}"

# Les intitulés varient avec la langue du terminal. On reconnaît sur des
# fragments plutôt que sur l'égalité stricte.
_COLONNES = {
    "resultat": ("result", "résultat", "resultat", "profit"),
    "trades": ("trades", "transactions", "deals"),
    "profit_factor": ("profit factor", "facteur de profit"),
    "drawdown": ("drawdown", "drawdown maximal", "perte"),
    "sharpe": ("sharpe",),
    "esperance_r": ("custom", "personnalisé", "personnalise"),
}


def lire_rapport(chemin: Path) -> list[Passe]:
    """Analyse le XML d'optimisation produit par MT5.

    Le format est le tableur XML d'Excel 2003 : une ligne d'en-têtes puis
    une ligne par passe. Les colonnes de gauche sont les statistiques, les
    suivantes les paramètres d'entrée — leur nombre varie donc selon la
    grille, et il faut les découvrir plutôt que les supposer.
    """
    chemin = Path(chemin)
    if not chemin.exists():
        return []

    # MT5 écrit ce rapport en UTF-16 ou en UTF-8 selon la version et la
    # locale. Décoder en UTF-16 un flux qui n'en est pas lève avant même
    # qu'on puisse regarder le contenu : il faut donc essayer, pas tester.
    txt = ""
    for codage in ("utf-16", "utf-8-sig", "utf-8", "cp1252"):
        try:
            txt = chemin.read_text(encoding=codage)
        except (UnicodeError, UnicodeDecodeError):
            continue
        if txt.lstrip().startswith("<?xml") or "<Workbook" in txt[:400]:
            break
    if not txt:
        txt = chemin.read_text(encoding="utf-8", errors="replace")

    try:
        racine = ET.fromstring(txt)
    except ET.ParseError:
        return []

    passes: list[Passe] = []
    for table in racine.iter(f"{_NS}Table"):
        lignes = list(table.iter(f"{_NS}Row"))
        if len(lignes) < 2:
            continue

        entetes = [_cellule(c) for c in lignes[0].iter(f"{_NS}Cell")]
        index = _mapper(entetes)

        for ligne in lignes[1:]:
            vals = [_cellule(c) for c in ligne.iter(f"{_NS}Cell")]
            if not any(vals):
                continue
            p = Passe()
            for champ, i in index.items():
                if i < len(vals):
                    setattr(p, champ, _flottant(vals[i]))
            p.trades = int(p.trades)
            # Les paramètres de l'EA sont ceux qui portent le préfixe `Inp`.
            # Se contenter d'« en-tête non reconnu = paramètre » ferait
            # entrer les statistiques non mappées (Recovery Factor, Equity
            # DD %, Pass…) dans le jeu de réglages, et on rejouerait
            # ensuite des « paramètres » qui n'existent pas.
            for i, nom in enumerate(entetes):
                if nom.startswith("Inp") and i < len(vals):
                    p.params[nom] = _flottant(vals[i])
            passes.append(p)

    return passes


def _mapper(entetes: list[str]) -> dict[str, int]:
    index: dict[str, int] = {}
    for champ, fragments in _COLONNES.items():
        for i, e in enumerate(entetes):
            bas = e.strip().lower()
            if i in index.values():
                continue
            if any(f in bas for f in fragments):
                index[champ] = i
                break
    return index


def _cellule(cell) -> str:
    d = cell.find(f"{_NS}Data")
    return (d.text or "").strip() if d is not None else ""


def _flottant(txt: str) -> float:
    if not txt:
        return 0.0
    # MT5 écrit selon la locale : espaces fines de milliers, virgule
    # décimale. Les deux cassent `float()` sans nettoyage.
    net = txt.replace(" ", "").replace(" ", "").replace("%", "")
    if "," in net and "." not in net:
        net = net.replace(",", ".")
    else:
        net = net.replace(",", "")
    try:
        return float(net)
    except ValueError:
        return 0.0


def retenir(passes: list[Passe], *, min_trades: int = 30,
            min_pf: float = 1.2, top: int = 10) -> list[Passe]:
    """Filtre puis trie — les viables d'abord, la meilleure espérance ensuite."""
    bons = [p for p in passes if p.viable(min_trades=min_trades, min_pf=min_pf)]
    bons.sort(key=lambda p: p.esperance_r, reverse=True)
    return bons[:top]
