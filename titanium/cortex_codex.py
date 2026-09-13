"""Le bassin Codex : un second cortex, adosse au CLI d'abonnement Codex.

Etape C3 du dossier cortex. Claude et Codex ne sont pas bons au meme endroit :
Codex **contraint sa reponse finale par un JSON Schema** (`--output-schema`), ce
qui supprime la premiere cause de `WAIT` involontaire — un JSON illisible. Le
pool Claude continue de demander la meme enveloppe en prose ; le pool Codex la
fait respecter par l'outil.

**Ce que ce module possede, et ce qu'il ne possede pas.** Il possede la FORME
du verdict : quel fichier lire, comment le lire, et contre quel schema le
valider. Il ne possede NI le lancement du CLI, NI le disjoncteur, NI la
classification d'une panne, NI la distinction entre refus prealable et panne :
le lancement appartient a `titanium.cortex_cli`, et le reste a `hermes_cortex`,
chacun proprietaire unique. Deux modules qui classifient la meme panne, ou qui
lancent le meme genre de CLI, finiraient par diverger — c'est precisement le
defaut que l'etape C1 a corrige.

Corollaire : **`executer` ne choisit pas son environnement.** Le lancement purge
les identifiants d'API parce que c'est `cortex_cli.lancer` qui le fait, pour tous
les bassins. Il n'y a donc pas de parametre a oublier ici.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from titanium import cortex_cli

#: Les noms de fournisseur que ce module sert. Un nom, pas une convention :
#: `hermes_cortex` decide par appartenance a ce tuple, jamais par sous-chaine.
NOMS = ("codex-cli",)


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


def charger_schema() -> dict[str, Any]:
    """Le schema versionne. Un seul chargement, un seul proprietaire."""
    charge = json.loads(cortex_cli.SCHEMA_VERDICT.read_text(encoding="utf-8"))
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

    `-o` est la source fiable : la documentation de `codex exec` est explicite —
    le fichier recoit le message final, et `stdout` le repete. Le repli sur
    `stdout` couvre le cas ou le fichier n'a pas ete ecrit : le texte sera alors
    juge non conforme, ce qui est le bon verdict, mais le motif nommera la vraie
    cause.
    """
    try:
        return sortie.read_text(encoding="utf-8")
    except OSError:
        return stdout


def executer(prompt: str, *, timeout_s: float) -> dict[str, Any]:
    """Lance le CLI Codex et rend la charge validee, ou leve.

    Le lancement — binaire, ligne de commande, environnement purge — appartient
    a `cortex_cli`. Ce module ne fait que demander, puis LIRE la reponse selon
    son contrat.
    """
    with tempfile.TemporaryDirectory(prefix="codex-cortex-") as dossier:
        sortie = Path(dossier) / "verdict.json"
        try:
            rendu = cortex_cli.lancer("codex-cli", prompt,
                                      timeout_s=timeout_s, sortie=sortie)
        except (cortex_cli.BassinIntrouvable, cortex_cli.EchecBassin) as exc:
            raise CodexEchec(str(exc)) from exc
        if rendu.returncode != 0:
            raise CodexEchec(
                "codex exec a echoue",
                returncode=rendu.returncode,
                stdout=rendu.stdout,
                stderr=rendu.stderr,
            )
        brut = _reponse_du_cli(sortie, rendu.stdout)
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
