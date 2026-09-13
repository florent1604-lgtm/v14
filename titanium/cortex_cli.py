"""Le lanceur unique des bassins adosses a un CLI.

Un seul endroit resout l'executable, construit la ligne de commande, purge
l'environnement et lance le sous-processus.

DEUX copies de cette regle ont coexiste, une par bassin, avec deux strategies de
shim Windows differentes — `python -c "from hermes_cli.main import main; main()"`
d'un cote, `cmd /c` de l'autre. C'est la forme de duplication que ce depot paie
deja ailleurs : `titanium/sizing.py` documente qu'une copie divergente a produit
un univers faux pendant des semaines. Ici la consequence serait plus discrete et
du meme ordre — un bassin joignable par un chemin et pas par l'autre, et un
environnement purge par un appelant mais pas par le suivant.

CE QUE CE MODULE POSSEDE
------------------------

* la resolution du binaire : surcharge par variable, puis installation locale,
  puis PATH ;
* la ligne de commande, drapeaux compris ;
* la purge des identifiants d'API, qui EST la regle d'abonnement ;
* le lancement du sous-processus et ses options.

CE QU'IL NE POSSEDE PAS
-----------------------

* la classification d'une panne, la portee d'un refus, la quarantaine et le
  repli : `hermes_cortex.interroger_bassins` en est le proprietaire unique, et
  c'est la SEULE porte d'entree d'un appel fournisseur ;
* la forme du verdict attendu : `cortex_codex` possede le schema et la lecture
  du fichier de sortie.

**Aucun appelant ne choisit son environnement.** `lancer` purge toujours : il
n'existe pas de parametre a oublier, donc pas de chemin qui lancerait un CLI avec
une cle API dans l'environnement et le ferait facturer a la cle.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Le schema de verdict du bassin Codex. Il vit ici parce qu'il est un ARGUMENT
#: de la ligne de commande (`--output-schema`) : le chemin doit etre connu du
#: constructeur de cette ligne, et un second chemin ailleurs deriverait.
SCHEMA_VERDICT = ROOT / "config" / "schema_verdict_cortex.json"

#: Les variables qui font ABANDONNER l'abonnement a un CLI.
#:
#:   ANTHROPIC_*  le CLI Hermes bascule sur une cle API dont le solde est vide
#:                (cause racine mesuree le 08/09) ;
#:   OPENAI_*     la documentation Codex est explicite : `codex exec` reutilise
#:   CODEX_*      l'authentification sauvegardee par defaut, mais facture la cle
#:                des que `OPENAI_API_KEY` ou `CODEX_API_KEY` est dans
#:                l'environnement. C'est le meme mode de panne, transpose.
IDENTIFIANTS_API = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY", "CODEX_API_KEY",
)

_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class BassinIntrouvable(RuntimeError):
    """Le binaire d'un bassin n'existe pas sur cette machine.

    Ce n'est PAS une panne du fournisseur : rien n'a ete appele. L'appelant ne
    doit donc pas ouvrir de disjoncteur pour son compte.
    """


class EchecBassin(RuntimeError):
    """Le processus n'a pas pu etre lance, ou n'a pas rendu la main a temps.

    Distinct d'un code de retour non nul : celui-la est une REPONSE du bassin et
    se lit dans `Sortie`. Ici, le bassin n'a rien dit.
    """


@dataclass(frozen=True)
class Sortie:
    """Ce qu'un bassin a rendu, brut et non interprete."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Regle:
    """Comment joindre un bassin : son binaire, et le shim Windows qui va avec.

    Publique a dessein : un troisieme CLI d'abonnement se DECLARE ici, en une
    entree, et herite aussitot de la resolution, de la purge d'environnement, du
    lancement et de toute la politique de repli. L'alternative — un chemin de
    lancement parallele — est precisement ce que ce module existe pour fermer.
    """

    nom: str
    #: Variable d'environnement qui force le binaire, si le PATH ne suffit pas.
    variable: str
    #: Noms cherches sur le PATH, dans l'ordre.
    binaires: tuple[str, ...]
    #: Chemin sous `%LOCALAPPDATA%`, pour une installation utilisateur (Windows).
    sous_local: tuple[str, ...] = ()
    #: `"module"` : le shim `.exe` est bloque par Windows, on passe par l'entree
    #: Python du paquet. `"cmd"` : npm installe un `.cmd`, que `CreateProcess`
    #: ne sait pas lancer seul.
    shim: str = "cmd"
    module: str = ""


#: Les bassins adosses a un CLI. L'ordre n'a pas de sens ici : c'est la liste de
#: repli de `hermes_cortex` qui decide qui est essaye d'abord.
REGLES: dict[str, Regle] = {
    "claude-cli": Regle(
        nom="claude-cli",
        variable="HERMES_CORTEX_EXECUTABLE",
        binaires=("hermes.exe",),
        sous_local=("hermes", "hermes-agent", "venv", "Scripts", "python.exe"),
        shim="module",
        module="from hermes_cli.main import main; main()",
    ),
    "codex-cli": Regle(
        nom="codex-cli",
        variable="CODEX_CORTEX_EXECUTABLE",
        binaires=("codex", "codex.cmd", "codex.exe"),
        shim="cmd",
    ),
}


def _regle(bassin: str) -> Regle:
    try:
        return REGLES[bassin]
    except KeyError as exc:
        raise BassinIntrouvable(f"bassin CLI inconnu: {bassin}") from exc


def purger_environnement() -> dict[str, str]:
    """L'environnement d'un sous-processus, purge des identifiants d'API.

    CAUSE RACINE MESUREE LE 08/09/2026, et correction du diagnostic du 07/09.

    Le CLI Hermes, des qu'il voit `ANTHROPIC_API_KEY` dans son environnement,
    abandonne l'abonnement Claude Pro/Max et facture la CLE API — dont le solde
    est vide. Le fournisseur repond alors « HTTP 400: credit balance is too
    low ». Le message etait exact ; il parlait simplement d'un compte que V14 ne
    doit jamais utiliser. Preuve, meme prompt et meme binaire :

        sans ANTHROPIC_API_KEY   rc=0, verdicts JSON, ~10 s
        avec ANTHROPIC_API_KEY   HTTP 401: API key is invalid

    Le chainon : `PRIME_V14.bat` exporte la cle depuis `.env` ; tout service
    lance depuis un shell ayant execute PRIME en herite, `cmd /k` la propageant
    au worker. D'ou l'intermittence — elle dependait du shell de depart, pas
    d'une « fenetre d'usage » du fournisseur.

    Ce que cela invalide : l'enquete du 07/09 concluait a un quota glissant qui
    « se recharge ». Ce qui se rechargeait, c'etait le shell d'ou l'on
    relancait. `HERMES_LOT_MAX` et `HERMES_INTERVALLE_MIN_S` ont ete calibres
    contre une cause inexistante ; ils restent en place car ils bornent
    utilement le debit, mais ils ne soignent pas ce defaut.

    **La purge vit ICI, dans le lanceur, pour qu'aucun chemin d'appel — entrees,
    positions, diagnostic — ne puisse la contourner, et qu'aucun shell parent ne
    puisse la defaire.** Une copie chez l'appelant aurait derive, et un appelant
    qui l'oublie facture la cle sans que rien ne le dise.

    La documentation Codex decrit le meme piege pour `codex exec` : une seule
    liste, donc, pour les deux CLI.

    Les valeurs ne sont ni lues, ni journalisees : les cles sont retirees, jamais
    inspectees.
    """
    env = dict(os.environ)
    for cle in IDENTIFIANTS_API:
        env.pop(cle, None)
    return env


def executable(bassin: str) -> Path:
    """Le binaire d'un bassin : surcharge, puis installation locale, puis PATH."""
    regle = _regle(bassin)
    candidats: list[Path] = []
    force = os.getenv(regle.variable, "").strip()
    if force:
        candidats.append(Path(force))
    if regle.sous_local and os.name == "nt":
        local = os.getenv("LOCALAPPDATA", "").strip()
        if local:
            candidats.append(Path(local).joinpath(*regle.sous_local))
    for nom in regle.binaires:
        trouve = shutil.which(nom)
        if trouve:
            candidats.append(Path(trouve))
    for candidat in candidats:
        if candidat.is_file():
            return candidat
    raise BassinIntrouvable(f"executable {bassin} introuvable")


def prefixe(bassin: str) -> list[str]:
    """Le debut de la ligne de commande, executable compris.

    Une seule regle de shim pour les deux bassins, decidee par ce qu'ils sont et
    non par l'endroit qui les appelle.
    """
    binaire = executable(bassin)
    regle = _regle(bassin)
    if regle.shim == "module":
        if os.name == "nt" and binaire.name.lower() == "python.exe":
            return [str(binaire), "-c", regle.module]
        return [str(binaire)]
    if os.name == "nt" and binaire.suffix.lower() in {".cmd", ".bat"}:
        return ["cmd", "/c", str(binaire)]
    return [str(binaire)]


#: Les drapeaux du bassin Codex, et pourquoi chacun est la.
#:
#: `--ephemeral` : aucune session persistee, un cortex en boucle n'a pas a
#:               laisser des rollouts derriere lui ;
#: `--ignore-user-config` et `--ignore-rules` : un `config.toml` ou un `.rules`
#:               de la machine ne doivent pas pouvoir changer le verdict d'un
#:               composant qui publie des politiques ;
#: `--sandbox read-only` : le cortex lit, il ne modifie rien.
#:
#: `--json` N'EST PAS la, et c'est deliberé. Le drapeau transforme stdout en flux
#: JSONL, mais personne ne lit ces evenements : le verdict vient de `-o`, et le
#: message final sur stdout sert de repli. L'activer aurait ete de la machinerie
#: sans lecteur ; l'etape C6 l'ajoutera avec celui qui le lit.
DRAPEAUX_CODEX = (
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
    "--sandbox", "read-only",
)


def commande(bassin: str, prompt: str, *, sortie: Path | None = None,
             modele: str | None = None) -> list[str]:
    """La ligne de commande complete, en un seul endroit.

    Le prompt est un ARGUMENT et non une entree standard : `HERMES_LOT_MAX` borne
    deja sa taille, et le garder dans `argv` permet a un test de relire ce qui a
    ete envoye au lieu de ce que le code croit envoyer.
    """
    regle = _regle(bassin)
    prefixe_lancement = prefixe(bassin)
    if regle.shim == "module":
        return [
            *prefixe_lancement,
            "-z", prompt,
            "--provider", bassin,
            "--model", modele or "",
            "--ignore-rules",
            "-t", "todo",
        ]
    return [
        *prefixe_lancement,
        "exec", prompt,
        *DRAPEAUX_CODEX,
        "--output-schema", str(SCHEMA_VERDICT),
        "-o", str(sortie),
    ]


def lancer(bassin: str, prompt: str, *, timeout_s: float,
           sortie: Path | None = None, modele: str | None = None) -> Sortie:
    """Lance un bassin et rend ce que le processus a rendu, sans l'interpreter.

    Le code de retour est RENDU, pas juge : un `returncode != 0` est une reponse
    du bassin — souvent la seule qui porte le motif — et c'est l'appelant qui la
    classe avec le vocabulaire commun de `hermes_cortex`.

    Leve `BassinIntrouvable` si le binaire n'existe pas, et `EchecBassin` si le
    processus n'a pas pu etre lance ou n'a pas rendu la main a temps.
    """
    ligne = commande(bassin, prompt, sortie=sortie, modele=modele)
    try:
        termine = subprocess.run(
            ligne,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, float(timeout_s)),
            check=False,
            creationflags=_FLAGS,
            env=purger_environnement(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EchecBassin(type(exc).__name__) from exc
    return Sortie(termine.returncode, termine.stdout, termine.stderr)
