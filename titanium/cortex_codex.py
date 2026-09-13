"""Le bassin Codex : un second cortex, adosse au CLI d'abonnement Codex.

Etape C3 du dossier cortex. Claude et Codex ne sont pas bons au meme endroit :
Codex **contraint sa reponse finale par un JSON Schema** (`--output-schema`), ce
qui supprime la premiere cause de `WAIT` involontaire — un JSON illisible. Le
pool Claude continue de demander la meme enveloppe en prose ; le pool Codex la
fait respecter par l'outil.

**Ce que ce module possede, et ce qu'il ne possede pas.** Il possede le
transport : la ligne de commande, le sous-processus, la lecture du fichier de
reponse et sa validation contre le schema versionne. Il ne possede NI le
disjoncteur, NI la classification d'une panne, NI la distinction entre refus
prealable et panne : tout cela vit chez `hermes_cortex`, qui en est le
proprietaire unique. Deux modules qui classifient la meme panne finiraient par
diverger, et c'est precisement le defaut que l'etape C1 a corrige.

Corollaire : **l'environnement du sous-processus est fourni par l'appelant.**
`hermes_cortex._env_abonnement` en est le proprietaire, parce que la regle est
la meme pour les deux CLI et qu'une seconde purge ici deriverait de la premiere.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

#: Les noms de fournisseur que ce module sert. Un nom, pas une convention :
#: `hermes_cortex` decide par appartenance a ce tuple, jamais par sous-chaine.
NOMS = ("codex-cli",)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "config" / "schema_verdict_cortex.json"

_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

#: Drapeaux que le CLI doit recevoir, et pourquoi chacun est la.
#:
#: `--json`      mode lisible par machine : les erreurs du fournisseur arrivent
#:               sur stdout sous forme d'evenements, la ou `_safe_cli_error` les
#:               classe. Personne ne lit encore `turn.completed.usage` ; l'etape
#:               C6 (budget de jetons) lui donnera son lecteur, et ce drapeau est
#:               deja celui que le dossier ordonne.
#: `--ephemeral` aucune session persistee sur disque : un cortex qui tourne en
#:               boucle n'a aucune raison de laisser des rollouts derriere lui.
#: `--ignore-user-config` et `--ignore-rules` : la configuration utilisateur et
#:               les regles execpolicy de la machine ne doivent pas pouvoir
#:               changer le verdict d'un composant qui publie des politiques.
#: `--sandbox read-only` : le cortex lit, il ne modifie rien. Le defaut est deja
#:               read-only, mais l'ecrire evite qu'un `config.toml` utilisateur
#:               l'elargisse en silence.
DRAPEAUX = (
    "--json",
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
    "--sandbox", "read-only",
)


class CodexEchec(RuntimeError):
    """Le CLI Codex n'a pas rendu de verdict exploitable.

    Porte `stdout`, `stderr` et `returncode` **bruts** : classer ici ferait
    diverger deux vocabulaires pour la meme panne. La classification appartient
    a `hermes_cortex`, qui la partage avec le pool Claude.
    """

    def __init__(self, message: str, *, returncode: int = -1,
                 stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class CodexHorsSchema(CodexEchec):
    """Le CLI a repondu, mais hors du contrat.

    Ce n'est **pas** une panne : le fournisseur est en bonne sante, c'est la
    reponse qui est inutilisable. Ouvrir le disjoncteur ici punirait un service
    disponible, et reessayer avec un lot plus petit ne repare pas une forme
    fausse. L'appelant tombe donc en `WAIT` — le fail-closed que le dossier
    exige pour une charge hors schema — et le repli peut interroger l'autre
    bassin, ce qui est exactement a quoi sert un second bassin.
    """


def _executable() -> Path:
    """Le binaire Codex. `CODEX_CORTEX_EXECUTABLE` d'abord, puis le PATH."""
    explicite = os.getenv("CODEX_CORTEX_EXECUTABLE", "").strip()
    candidats = [Path(explicite)] if explicite else []
    for nom in ("codex", "codex.cmd", "codex.exe"):
        trouve = shutil.which(nom)
        if trouve:
            candidats.append(Path(trouve))
    for candidat in candidats:
        if candidat.is_file():
            return candidat
    raise CodexEchec("executable Codex introuvable")


def _prefixe() -> list[str]:
    """Le debut de la ligne de commande, executable compris.

    npm installe Codex sous forme de shim `.cmd` sur Windows, et `CreateProcess`
    ne sait pas lancer un `.cmd` seul. C'est la meme classe de probleme que le
    shim Hermes documente dans `hermes_cortex._hermes_command_prefix` : passer
    par `cmd /c` est la seule facon de l'executer sans shell interactif.
    """
    executable = _executable()
    if os.name == "nt" and executable.suffix.lower() in {".cmd", ".bat"}:
        return ["cmd", "/c", str(executable)]
    return [str(executable)]


def _commande(prompt: str, sortie: Path, *, schema: Path | None = None) -> list[str]:
    """La ligne de commande reellement construite, en un seul endroit.

    Le prompt est un ARGUMENT et non une entree standard : `HERMES_LOT_MAX = 1`
    borne deja sa taille, et le garder dans `argv` permet a un test de relire ce
    qui a ete envoye au lieu de ce que le code croit envoyer.
    """
    return [
        *_prefixe(),
        "exec",
        prompt,
        *DRAPEAUX,
        "--output-schema", str(schema or SCHEMA),
        "-o", str(sortie),
    ]


def charger_schema() -> dict[str, Any]:
    """Le schema versionne. Un seul chargement, un seul proprietaire."""
    charge = json.loads(SCHEMA.read_text(encoding="utf-8"))
    if not isinstance(charge, dict):
        raise CodexHorsSchema("schema de verdict illisible")
    return charge


def _du_type(valeur: Any, attendu: str) -> bool:
    if attendu == "object":
        return isinstance(valeur, dict)
    if attendu == "array":
        return isinstance(valeur, list)
    if attendu == "string":
        return isinstance(valeur, str)
    if attendu == "boolean":
        return isinstance(valeur, bool)
    if attendu == "integer":
        return isinstance(valeur, int) and not isinstance(valeur, bool)
    if attendu == "number":
        return isinstance(valeur, (int, float)) and not isinstance(valeur, bool)
    return True


def _motifs(valeur: Any, schema: dict[str, Any], chemin: str) -> list[str]:
    """Les motifs de non-conformite d'une valeur. Vide = conforme.

    Sous-ensemble de JSON Schema volontairement borne a ce que le schema
    versionne emploie : `jsonschema` n'est pas une dependance du depot, et
    l'ajouter pour cinq mots-cles ferait bouger la CI pour rien. Ce que le
    validateur ne comprend pas, il ne le refuse pas — il ne le juge pas.
    """
    out: list[str] = []
    attendu = schema.get("type")
    if attendu is not None and not _du_type(valeur, attendu):
        return [f"{chemin}: type {attendu} attendu"]
    if "enum" in schema and valeur not in schema["enum"]:
        return [f"{chemin}: hors de {schema['enum']}"]
    branches = schema.get("anyOf")
    if branches and all(_motifs(valeur, branche, chemin) for branche in branches):
        # Append et NON return : l'echec d'`anyOf` est une contrainte parmi
        # d'autres au meme niveau, et s'arreter ici masquait les champs requis
        # absents derriere un motif de variantes. Un operateur qui lit « aucune
        # des 2 variantes » sans savoir QUI manque doit renvoyer la reponse au
        # modele pour rien. Les deux motifs partent donc ensemble.
        out.append(f"{chemin}: aucune des {len(branches)} variantes acceptees")
    if isinstance(valeur, dict):
        for cle in schema.get("required", []):
            if cle not in valeur:
                out.append(f"{chemin}: champ requis absent ({cle})")
        propres = schema.get("properties") or {}
        for cle, sous_valeur in valeur.items():
            if cle in propres:
                out.extend(_motifs(sous_valeur, propres[cle], f"{chemin}.{cle}"))
            elif schema.get("additionalProperties") is False:
                out.append(f"{chemin}: champ inconnu ({cle})")
    if isinstance(valeur, list):
        minimum = schema.get("minItems")
        if minimum is not None and len(valeur) < minimum:
            out.append(f"{chemin}: moins de {minimum} element(s)")
        if "items" in schema:
            for rang, item in enumerate(valeur):
                out.extend(_motifs(item, schema["items"], f"{chemin}[{rang}]"))
    if isinstance(valeur, str):
        court, long = schema.get("minLength"), schema.get("maxLength")
        if court is not None and len(valeur) < court:
            out.append(f"{chemin}: chaine trop courte")
        if long is not None and len(valeur) > long:
            out.append(f"{chemin}: chaine trop longue")
    if isinstance(valeur, (int, float)) and not isinstance(valeur, bool):
        mini, maxi = schema.get("minimum"), schema.get("maximum")
        if mini is not None and valeur < mini:
            out.append(f"{chemin}: sous le minimum")
        if maxi is not None and valeur > maxi:
            out.append(f"{chemin}: au-dessus du maximum")
    return out


def valider(charge: Any) -> list[str]:
    """Valide une charge contre le schema versionne et rend ses motifs.

    Rend les motifs et non un booleen : c'est le motif qui part dans le tunnel
    et qui est cite par le disjoncteur. Un booleen obligerait chaque appelant a
    reinventer un message, et les messages divergeraient.

    Aucun appelant ne fournit de schema : le contrat est versionne dans
    `config/`, et une couture qui permettrait de le remplacer serait une porte
    ouverte a un cortex qui valide contre autre chose que ce qu'il publie.
    """
    return _motifs(charge, charger_schema(), "$")


def _reponse_du_cli(sortie: Path, stdout: str) -> str:
    """Le texte de verdict : le fichier `-o`, sinon la sortie standard.

    `-o` est la source fiable — avec `--json`, `stdout` est un flux
    d'evenements JSONL et non la reponse finale. Le repli sur `stdout` couvre le
    cas ou le fichier n'a pas ete ecrit : le texte sera alors juge non conforme,
    ce qui est le bon verdict, mais le motif nommera la vraie cause.
    """
    try:
        return sortie.read_text(encoding="utf-8")
    except OSError:
        return stdout


def executer(prompt: str, *, timeout_s: float, env: dict[str, str]) -> dict[str, Any]:
    """Lance le CLI Codex et rend la charge validee, ou leve.

    `env` est FOURNI par l'appelant. C'est ce qui garde la purge des
    identifiants API a un seul endroit : elle vaut pour les deux CLI, et la
    dupliquer ici la ferait deriver.
    """
    with tempfile.TemporaryDirectory(prefix="codex-cortex-") as dossier:
        sortie = Path(dossier) / "verdict.json"
        commande = _commande(prompt, sortie)
        try:
            termine = subprocess.run(
                commande,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1.0, float(timeout_s)),
                check=False,
                creationflags=_FLAGS,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CodexEchec(type(exc).__name__) from exc
        if termine.returncode != 0:
            raise CodexEchec(
                "codex exec a echoue",
                returncode=termine.returncode,
                stdout=termine.stdout,
                stderr=termine.stderr,
            )
        brut = _reponse_du_cli(sortie, termine.stdout)
        try:
            charge = json.loads(brut)
        except (ValueError, TypeError) as exc:
            # Avec `--output-schema`, un texte non-JSON est deja une rupture de
            # contrat : c'est le seul cas ou le parsing strict remplace le
            # parsing tolerant du pool Claude, et c'est l'interet du pool.
            raise CodexHorsSchema("reponse Codex sans JSON") from exc
        motifs = valider(charge)
        if motifs:
            raise CodexHorsSchema("; ".join(motifs)[:240])
        return charge
