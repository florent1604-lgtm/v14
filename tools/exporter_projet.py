"""Exporte tout le code source de V14 dans un JSON transmissible.

    python tools/exporter_projet.py            # → V14_export.json
    python tools/exporter_projet.py --sortie X.json

Destiné à être remis à un autre modèle pour étude. Il porte donc TOUT le
code et TOUS les commentaires — les commentaires disent le pourquoi, et sans
eux un relecteur externe re-proposerait les pièges déjà rencontrés.

Sur les secrets
---------------
Exclure `.env` ne suffit pas. Une clé d'API peut être codée en dur dans un
script, collée dans un test, laissée dans un exemple. Ce module **balaie le
contenu de chaque fichier** et caviarde ce qui ressemble à un secret, avant
d'écrire quoi que ce soit. Ce qui a été caviardé est compté et affiché : un
caviardage silencieux empêcherait de vérifier qu'il a bien eu lieu.

Le principe retenu est de caviarder trop plutôt que trop peu. Un faux
positif coûte une ligne illisible dans un fichier d'étude ; un faux négatif
publie un identifiant de compte de trading.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

#: Arborescences sans intérêt pour l'étude, ou trop volumineuses.
DOSSIERS_EXCLUS = {
    ".venv", "venv", "__pycache__", ".git", ".pytest_cache", ".ruff_cache",
    "node_modules", "results", "dist", "build", ".mypy_cache", ".idea",
    ".vscode", "eval_results", "dataflows_cache",
}

#: Extensions à embarquer. Tout le reste est ignoré — un `.ex5` compilé ou
#: une capture d'écran n'apprend rien à un relecteur.
EXTENSIONS = {
    ".py", ".mq5", ".mqh", ".md", ".txt", ".js", ".css", ".html",
    ".toml", ".cfg", ".ini", ".yaml", ".yml", ".json", ".sh", ".bat",
}

#: Fichiers exclus nommément. `.env` porte le mot de passe du compte.
FICHIERS_EXCLUS = {".env", ".env.local", ".env.production"}

#: Fichiers JSON de données, exclus car volumineux et sans valeur d'étude.
JSON_EXCLUS = {"package-lock.json", "titanium_zones.json", "positions.json"}

#: Motifs de secret. Larges à dessein.
MOTIFS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "clé OpenAI"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "clé Anthropic"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "clé Google"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "jeton GitHub"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "jeton Slack"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "clé AWS"),
    # Affectation d'un littéral à un nom de variable sensible. On exige un
    # `=` ou `:` SUIVI d'une valeur, et on refuse les virgules, pour ne pas
    # confondre avec un tuple de noms de paramètres — `"api_key",
    # "callbacks"` n'est pas un secret, et le signaler ferait douter de
    # chaque autre signalement.
    (re.compile(r"(?i)(?:api[_-]?key|auth[_-]?token|secret|password|passwd|"
                r"mot[_-]?de[_-]?passe)\s*[=:]\s*[\"']([^\"'\n,]{8,})[\"']"),
     "affectation"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "clé privée"),
]

#: Valeurs qui ne sont PAS des secrets malgré leur forme.
BLANCHE = {
    "your_api_key_here", "changeme", "xxx", "none", "null", "true", "false",
    "your_key", "placeholder", "example", "sk-xxx", "os.getenv", "getenv",
    "password", "api_key", "token", "secret", "optional", "required",
}


def valeurs_a_masquer() -> dict[str, str]:
    """Valeurs RÉELLES lues dans `.env`, à masquer où qu'elles apparaissent.

    `.env` est exclu de l'export, mais ses valeurs se retrouvent ailleurs :
    dans la documentation, dans un exemple, dans un commentaire. Le numéro
    de compte de trading a ainsi été retrouvé dans `CLAUDE.md` lors de
    l'audit du premier export.

    On ne masque QUE les valeurs suffisamment spécifiques pour ne pas
    provoquer de dégâts collatéraux — masquer « 1 » remplacerait la moitié
    du code source.
    """
    masques: dict[str, str] = {}
    env = RACINE / ".env"
    if not env.exists():
        return masques
    interessants = {
        "TITANIUM_DEMO_LOGIN": "«COMPTE-MASQUÉ»",
        "TITANIUM_DEMO_PASSWORD": "«SECRET-CAVIARDÉ»",
        "TITANIUM_REAL_LOGIN": "«COMPTE-MASQUÉ»",
    }
    for ligne in env.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in ligne or ligne.lstrip().startswith("#"):
            continue
        cle, _, val = ligne.partition("=")
        cle, val = cle.strip(), val.strip().strip("\"'")
        # Sous 6 caractères, une valeur est trop commune pour être masquée
        # sans casser du code sans rapport.
        if cle in interessants and len(val) >= 6:
            masques[val] = interessants[cle]
        # Toute clé d'API réelle, quel que soit son nom.
        elif cle.upper().endswith(("_API_KEY", "_TOKEN")) and len(val) >= 16:
            masques[val] = "«SECRET-CAVIARDÉ»"
    return masques


MASQUES = valeurs_a_masquer()


def _caviarder(texte: str, chemin: str) -> tuple[str, list[str]]:
    """Remplace les secrets par une marque explicite. Rend (texte, trouvés)."""
    trouves: list[str] = []

    # ── D'abord les valeurs réelles connues : c'est le filet le plus sûr,
    #    car il ne repose sur aucune heuristique de forme.
    for valeur, remplacement in MASQUES.items():
        if valeur and valeur in texte:
            texte = texte.replace(valeur, remplacement)
            trouves.append(f"valeur de .env · {chemin}")

    for motif, libelle in MOTIFS:
        def _remplacer(m: re.Match) -> str:
            entier = m.group(0)
            # Pour les affectations, seule la VALEUR est un secret potentiel.
            valeur = m.group(1) if m.lastindex else entier
            if valeur.strip().strip("\"'").lower() in BLANCHE:
                return entier
            if valeur.startswith(("os.", "config", "self.", "cfg")):
                return entier          # lecture de variable, pas un littéral
            trouves.append(f"{libelle} · {chemin}")
            if m.lastindex:
                return entier.replace(valeur, "«SECRET-CAVIARDÉ»")
            return "«SECRET-CAVIARDÉ»"

        texte = motif.sub(_remplacer, texte)

    return texte, trouves


def _a_garder(p: Path) -> bool:
    if any(part in DOSSIERS_EXCLUS for part in p.parts):
        return False
    if p.name in FICHIERS_EXCLUS or p.name in JSON_EXCLUS:
        return False
    if p.name.startswith(".env") and p.name != ".env.example":
        return False
    if p.suffix.lower() not in EXTENSIONS:
        return False
    # Les JSON de résultats sont des données, pas du code.
    if p.suffix == ".json" and p.stat().st_size > 200_000:
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sortie", default=str(RACINE / "V14_export.json"))
    ap.add_argument("--racine", default=str(RACINE))
    a = ap.parse_args()

    racine = Path(a.racine).resolve()
    fichiers: list[dict] = []
    secrets: list[str] = []
    illisibles: list[str] = []
    total_lignes = 0

    for p in sorted(racine.rglob("*")):
        if not p.is_file() or not _a_garder(p):
            continue
        rel = p.relative_to(racine).as_posix()
        try:
            brut = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            try:
                brut = p.read_text(encoding="utf-16")
            except Exception:  # noqa: BLE001
                illisibles.append(rel)
                continue

        contenu, trouves = _caviarder(brut, rel)
        secrets += trouves
        lignes = contenu.count("\n") + 1
        total_lignes += lignes

        fichiers.append({
            "chemin": rel,
            "langage": {".py": "python", ".mq5": "mql5", ".mqh": "mql5",
                        ".md": "markdown", ".js": "javascript",
                        ".css": "css", ".html": "html"}.get(p.suffix, "texte"),
            "lignes": lignes,
            "octets": len(contenu.encode("utf-8")),
            "sha256": hashlib.sha256(contenu.encode("utf-8")).hexdigest()[:16],
            "contenu": contenu,
        })

    par_dossier: dict[str, int] = {}
    for f in fichiers:
        tete = f["chemin"].split("/")[0] if "/" in f["chemin"] else "(racine)"
        par_dossier[tete] = par_dossier.get(tete, 0) + 1

    charge = {
        "projet": "Titanium V14",
        "description": (
            "Bot de trading algorithmique. Socle multi-agents LLM (fork de "
            "TradingAgents) fusionné avec les briques déterministes de V12 : "
            "portes de confluence ET, RiskGate fail-closed, exécution MT5 "
            "avec mur démo↔réel, gestion de position breakeven/trailing."
        ),
        "regles_non_negociables": [
            "Aucun LLM n'a autorité d'exécution : il module la TAILLE de "
            "±25 % au plus, jamais la décision d'entrer ou de sortir.",
            "Aucun LLM dans le chemin temps réel : la délibération vit dans "
            "un processus séparé, couplée par fichier. Sa panne ne doit rien "
            "changer au comportement du trading.",
            "Fail-closed : une donnée manquante BLOQUE, elle n'autorise "
            "jamais. `edge_ok=None` vaut BLOCK en mode PROD.",
        ],
        "exporte_le": datetime.now(timezone.utc).isoformat(),
        "racine": str(racine),
        "statistiques": {
            "fichiers": len(fichiers),
            "lignes": total_lignes,
            "octets": sum(f["octets"] for f in fichiers),
            "par_dossier": dict(sorted(par_dossier.items(),
                                       key=lambda kv: -kv[1])),
        },
        "exclusions": {
            "note": (
                "`.env` est exclu : il porte les identifiants du compte de "
                "trading. Les dossiers d'environnement, de cache et de "
                "résultats sont exclus car ils ne contiennent pas de code."
            ),
            "dossiers": sorted(DOSSIERS_EXCLUS),
            "fichiers": sorted(FICHIERS_EXCLUS | JSON_EXCLUS),
            "illisibles": illisibles,
        },
        "caviardage": {
            "note": (
                "Le contenu de chaque fichier a été balayé à la recherche de "
                "secrets, pas seulement `.env`. Toute occurrence est "
                "remplacée par «SECRET-CAVIARDÉ»."
            ),
            "occurrences": len(secrets),
            "detail": sorted(set(secrets)),
        },
        "fichiers": fichiers,
    }

    sortie = Path(a.sortie)
    sortie.write_text(json.dumps(charge, ensure_ascii=False, indent=1),
                      encoding="utf-8")

    mo = sortie.stat().st_size / 1_048_576
    print(f"✓ {sortie}")
    print(f"  {len(fichiers)} fichiers · {total_lignes:,} lignes · {mo:.2f} Mo")
    print(f"  répartition : "
          + ", ".join(f"{k} {v}" for k, v in
                      list(charge['statistiques']['par_dossier'].items())[:6]))
    if secrets:
        print(f"\n  ⚠️  {len(secrets)} secret(s) caviardé(s) :")
        for s in sorted(set(secrets))[:10]:
            print(f"      {s}")
    else:
        print("\n  aucun secret détecté dans le code exporté")
    if illisibles:
        print(f"  {len(illisibles)} fichier(s) illisible(s), ignoré(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
