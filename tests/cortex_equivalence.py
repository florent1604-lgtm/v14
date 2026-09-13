"""Harnais d'equivalence des appelants de production du cortex.

Ce que ce harnais mesure, et pourquoi il existe
-----------------------------------------------

L'invocation d'un bassin CLI a eu TROIS proprietaires : `_hermes_command_prefix`
dans `hermes_cortex`, la resolution propre a `cortex_codex`, et un chemin de
diagnostic qui s'adressait a `_ask` sans passer par la politique. Ils ont ete
ramenes a un seul (`titanium.cortex_cli`). Le risque d'une telle unification
n'est pas de casser la suite : c'est de changer SILENCIEUSEMENT les verdicts que
la boucle recoit. Une suite verte ne le dirait pas, parce qu'aucun test ne
comparait des verdicts a ceux d'avant.

Ce harnais compare exactement cela : sur une entree FIGEE, les deux appelants de
production — `analyse_entries` et `analyse_positions`, donc `_ask_par_lots` avec
les drapeaux que la production emploie — doivent rendre les memes verdicts
qu'avant l'unification, et composer les memes requetes.

Comment la reference a ete produite
-----------------------------------

Le meme fichier a ete pose a cote d'un worktree de controle a la tete
`d53b32e` — AVANT l'unification — et execute avec `CORTEX_EQUIV_CAPTURE` :

    CORTEX_EQUIV_CAPTURE=tests/fixtures/cortex_equivalence_reference.json \\
        python -m pytest tests/test_cortex_equivalence.py -q

L'injection du faux CLI choisit le proprietaire qui existe (`_hermes_command_prefix`
la-bas, `cortex_cli.prefixe` ici), ce qui est le seul point qui devait differer :
ce qu'on compare, ce sont les entrees et les verdicts, pas les entrailles.

Ce que le harnais ne prouve pas
-------------------------------

Il ne prouve rien sur un vrai abonnement : le faux CLI est un processus Python
reel, mais ce n'est ni Claude ni Codex. Il ne prouve pas non plus la latence ni
la facturation.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from titanium import hermes_cortex as hc

try:  # La tete d'avant l'unification n'a pas ce module : c'est justement l'objet.
    from titanium import cortex_cli
except ImportError:  # pragma: no cover - chemin de capture historique
    cortex_cli = None  # type: ignore[assignment]

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cortex_equivalence_reference.json"

#: Le faux CLI. Il ne connait aucun fournisseur : il lit la CHARGE du prompt,
#: refuse au-dela de deux candidats (ce qui exerce la scission de lot) et rend un
#: verdict par reference, determine par le rang dans la charge.
#:
#: Deterministe par construction : la meme entree rend la meme sortie, donc une
#: divergence de verdict ne peut venir que du code qui a compose ou lu l'appel.
FAUX_CLI = '''\
"""Faux CLI : journalise chaque appel, puis repond selon la CHARGE recue."""

import hashlib
import json
import os
import sys

argv = sys.argv[1:]
prompt = ""
for drapeau in ("-z", "exec"):
    if drapeau in argv:
        prompt = argv[argv.index(drapeau) + 1]
        break

charge = {}
try:
    charge = json.loads(prompt.splitlines()[-1])
except (ValueError, IndexError):
    charge = {}

cle = "candidates" if "candidates" in charge else "positions"
items = charge.get(cle) or []
ref_cle = "decision_ref" if cle == "candidates" else "request_ref"

valeur_provider = ""
if "--provider" in argv:
    valeur_provider = argv[argv.index("--provider") + 1]

journal = os.environ.get("CORTEX_EQUIV_JOURNAL")
if journal:
    with open(journal, "a", encoding="utf-8") as flux:
        flux.write(json.dumps({
            "provider": valeur_provider,
            "refs": [str(item.get(ref_cle, "")) for item in items],
            "sha256_prompt": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        }, ensure_ascii=False) + "\\n")

if len(items) > 2:
    # Refus AVANT generation : la requete est en cause. La scission doit scinder.
    print("HTTP 400: request too large for this model")
    sys.exit(1)

if not items:
    print(json.dumps({"verdicts": []}))
    sys.exit(0)

def _verdict(item, rang):
    ref = str(item.get(ref_cle, ""))
    if cle == "candidates":
        action = "ALLOW" if rang % 2 == 0 else "BLOCK"
        return {"decision_ref": ref, "action": action,
                "confidence": 0.75 if action == "ALLOW" else 0.25,
                "summary": "essai deterministe rang " + str(rang)}
    etat = "CALM" if rang % 2 == 0 else "FEAR"
    return {"request_ref": ref, "state": etat,
            "confidence": 0.8 if etat == "CALM" else 0.2,
            "reason": "essai deterministe rang " + str(rang)}

print(json.dumps({"verdicts": [
    _verdict(item, rang) for rang, item in enumerate(items)
]}))
sys.exit(0)
'''

#: Les faits sont STUBBES : `collect` interroge des sources externes et rendrait
#: la mesure non reproductible. Ce qui est compare ici n'est pas la collecte mais
#: la composition des requetes et la lecture des verdicts, deux choses qui ne
#: dependent pas du reseau.
FAITS = {
    "BTCUSD": [
        hc.Evidence(source="VenueMicrostructure", text="carnet desequilibre acheteur",
                    observed_at="2026-09-13T10:00:00+00:00"),
        hc.Evidence(source="CoinGecko", text="dominance stable",
                    observed_at="2026-09-13T10:00:00+00:00"),
    ],
    "ETHUSD": [
        hc.Evidence(source="VenueMicrostructure", text="carnet vendeur",
                    observed_at="2026-09-13T10:00:00+00:00"),
    ],
}

ENTREES = [
    {"decision_ref": "entree-BTCUSD-1", "symbol": "BTCUSD", "side": 1,
     "mechanical_summary": "cassure de range", "context_key": "c1",
     "bar_time": "2026-09-13T09:45:00+00:00", "observations": {"rsi": 61.0}},
    # Meme symbole, sens OPPOSE : les deux verdicts ALLOW du faux CLI doivent
    # etre ramenes a WAIT par l'arbitrage de conflit directionnel.
    {"decision_ref": "entree-BTCUSD-2", "symbol": "BTCUSD", "side": -1,
     "mechanical_summary": "rejet de sommet", "context_key": "c1",
     "bar_time": "2026-09-13T09:45:00+00:00", "observations": {"rsi": 61.0}},
    {"decision_ref": "entree-ETHUSD-1", "symbol": "ETHUSD", "side": 1,
     "mechanical_summary": "pullback sur support", "context_key": "c2",
     "bar_time": "2026-09-13T09:45:00+00:00", "observations": {"rsi": 44.0}},
]

REVUES = [
    {"request_ref": "revue-BTCUSD-1", "ticket": "101", "symbol": "BTCUSD",
     "side": 1, "entry": 60000.0, "current": 60400.0, "sl": 59700.0,
     "tp": 61000.0, "fav_r": 0.4, "peak_fav_r": 0.8, "mae_r": -0.2,
     "observed_at": "2026-09-13T10:00:00+00:00", "context": {"phase": "suivi"}},
    {"request_ref": "revue-ETHUSD-1", "ticket": "102", "symbol": "ETHUSD",
     "side": -1, "entry": 2500.0, "current": 2490.0, "sl": 2530.0,
     "tp": 2440.0, "fav_r": 0.2, "peak_fav_r": 0.3, "mae_r": -0.1,
     "observed_at": "2026-09-13T10:00:00+00:00", "context": {"phase": "suivi"}},
]

#: Les cles qui portent l'INSTANT de la mesure et non le comportement. Les
#: comparer signalerait une difference d'horloge, pas une difference de verdict.
CLES_VOLATILES = ("rendered_at", "observed_at")


def injecter(monkeypatch: Any, script: Path) -> None:
    """Branche le faux CLI sur le proprietaire du lancement qui existe.

    C'est le seul point qui doit differer entre la tete capturee et la tete
    courante : le proprietaire a change, pas le contrat.
    """
    if hasattr(hc, "_hermes_command_prefix"):
        monkeypatch.setattr(hc, "_hermes_command_prefix",
                            lambda: [sys.executable, str(script)])
        return
    assert cortex_cli is not None
    monkeypatch.setattr(cortex_cli, "prefixe",
                        lambda _bassin: [sys.executable, str(script)])


def _normaliser(valeur: Any) -> Any:
    if isinstance(valeur, dict):
        return {cle: _normaliser(sous) for cle, sous in sorted(valeur.items())
                if cle not in CLES_VOLATILES}
    if isinstance(valeur, list):
        return [_normaliser(item) for item in valeur]
    return valeur


def empreinte(tmp_path: Path, monkeypatch: Any) -> dict[str, Any]:
    """Les verdicts des deux appelants de production, et les requetes composees."""
    script = tmp_path / "faux_cli_equivalence.py"
    script.write_text(FAUX_CLI, encoding="utf-8")
    journal = tmp_path / "equivalence.ndjson"
    injecter(monkeypatch, script)
    monkeypatch.setenv("CORTEX_EQUIV_JOURNAL", str(journal))
    monkeypatch.setattr(hc, "_evidence_by_symbol", lambda symboles: dict(FAITS))
    monkeypatch.setattr(hc, "HERMES_INTERVALLE_MIN_S", 0.0)
    monkeypatch.setattr(hc, "HERMES_RETENTATIVES", 1)
    monkeypatch.setattr(hc.time, "sleep", lambda _s: None)
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "claude-cli")
    monkeypatch.delenv("TITANIUM_HERMES_PROVIDERS", raising=False)
    hc._reset_circuits()

    entrees = _normaliser(hc.analyse_entries([dict(r) for r in ENTREES]))
    # Le journal est relu entre les deux : les appels d'entree ne doivent pas
    # etre confondus avec ceux de suivi.
    appels_entrees = _lire_journal(journal)
    journal.write_text("", encoding="utf-8")
    positions = _normaliser(hc.analyse_positions([dict(r) for r in REVUES]))
    appels_positions = _lire_journal(journal)

    return {
        "entrees": entrees,
        "appels_entrees": appels_entrees,
        "positions": positions,
        "appels_positions": appels_positions,
    }


def _lire_journal(chemin: Path) -> list[dict]:
    try:
        brut = chemin.read_text(encoding="utf-8")
    except OSError:
        return []
    return [json.loads(ligne) for ligne in brut.splitlines() if ligne.strip()]


def empreinte_du_fichier() -> dict[str, Any]:
    """La reference commitee, telle quelle."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def ecrire_reference(chemin: Path, valeur: dict[str, Any]) -> None:
    chemin.write_text(json.dumps(valeur, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")


def _capturer(destination: Path) -> int:
    """Ecrire l'empreinte de la tete courante — la reference, depuis l'ancienne.

    N'est jamais emprunte par la suite : c'est l'outil d'une seule operation,
    celle qui a produit la fixture. Il vit ici plutot que dans un script a part
    pour qu'il soit impossible que l'empreinte capturee et l'empreinte verifiee
    sortent de deux codes differents.
    """
    import tempfile

    import pytest

    with (
        tempfile.TemporaryDirectory(prefix="cortex-equiv-") as dossier,
        pytest.MonkeyPatch.context() as patch,
    ):
        valeur = empreinte(Path(dossier), patch)
    ecrire_reference(destination, valeur)
    print(f"reference ecrite: {destination}")
    return 0


if __name__ == "__main__":  # pragma: no cover - capture de la reference
    cible = os.environ.get("CORTEX_EQUIV_CAPTURE")
    if not cible:
        raise SystemExit(
            "CORTEX_EQUIV_CAPTURE=<chemin> requis pour capturer la reference"
        )
    raise SystemExit(_capturer(Path(cible)))
