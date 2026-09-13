"""Garde de commit : aucun secret, aucun fichier d'accès ne part au dépôt.

La veille qui lit le dépôt après coup détecte une fuite en moins d'une minute.
Ce n'est pas assez : entre l'écriture et la détection, la donnée existe dans un
commit, et un commit peut être poussé. Cette garde ferme la fenêtre au seul
endroit où elle peut l'être — **avant** que le commit existe.

CE QU'ELLE NE FAIT JAMAIS
-------------------------
Elle n'imprime aucune valeur. Un rapport qui cite la clé qu'il dénonce la
recopie dans la sortie du terminal, dans le journal du hook, et souvent dans
un presse-papier. On nomme le fichier, la ligne et la longueur ; cela suffit à
la retrouver et ne la divulgue pas.

Elle ne lit pas `.env` : elle refuse qu'il soit indexé, ce qui ne demande que
son nom.

POURQUOI EN PREMIER DANS LE HOOK
---------------------------------
Le lint et les tests de sûreté prennent une dizaine de secondes. Une garde
d'accès doit répondre avant eux : si un secret est indexé, rien d'autre n'a
d'importance, et l'opérateur doit le savoir tout de suite.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import PurePosixPath

#: Valeurs de secrets, reconnues par leur préfixe d'émetteur et leur longueur.
#: On ne cherche pas les mots « token » ou « password » : ils abondent
#: légitimement dans du code, et une garde qui crie à chaque occurrence finit
#: désarmée par celui qu'elle dérange.
MOTIF_SECRET = re.compile(
    r"(sk-[A-Za-z0-9]{16,}"
    r"|sk-ant-[A-Za-z0-9_-]{16,}"
    r"|AIza[0-9A-Za-z_-]{30,}"
    r"|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{30,}"
    r"|AKIA[0-9A-Z]{12,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)

#: Fichiers qui ne doivent jamais entrer dans l'historique, quel que soit leur
#: contenu. Comparés sur le nom, jamais ouverts.
NOMS_INTERDITS = {
    ".env", ".envrc", "auth.json", "credentials.json",
    "id_rsa", "id_ed25519", ".netrc", ".pgpass",
}
SUFFIXES_INTERDITS = (".pem", ".p12", ".pfx", ".key", ".keystore")
PREFIXES_INTERDITS = (".env.",)

#: `tests/test_hermes_abonnement.py` porte une clé FACTICE de 29 caractères qui
#: sert précisément à prouver que la vraie est purgée de l'environnement. La
#: bannir reviendrait à supprimer le test qui protège le secret.
EXCEPTIONS = {
    "tests/test_garde_secrets.py",
    "tests/test_hermes_abonnement.py",
}


def chemin_interdit(chemin: str) -> bool:
    """Ce chemin est-il un fichier d'accès, sur son seul nom ?"""
    nom = PurePosixPath(str(chemin).replace("\\", "/")).name
    bas = nom.lower()
    return (
        bas in NOMS_INTERDITS
        or bas.endswith(SUFFIXES_INTERDITS)
        or bas.startswith(PREFIXES_INTERDITS)
    )


def secrets_dans(texte: str) -> list[tuple[int, int]]:
    """Positions des secrets trouvés : ``[(ligne, longueur)]``, sans valeur.

    La valeur n'est pas retournée, pas même en interne : l'appelant ne peut
    donc pas l'imprimer par inadvertance.
    """
    trouves = []
    for n, ligne in enumerate(str(texte).splitlines(), 1):
        for m in MOTIF_SECRET.finditer(ligne):
            trouves.append((n, len(m.group(0))))
    return trouves


def _git(*a: str) -> str:
    return subprocess.run(["git", *a], capture_output=True, text=True,
                          errors="replace", timeout=120).stdout


def fichiers_indexes() -> list[str]:
    sortie = _git("diff", "--cached", "--name-only", "--diff-filter=ACM")
    return [ligne.strip() for ligne in sortie.splitlines() if ligne.strip()]


def contenu_indexe(chemin: str) -> str:
    """Contenu tel qu'il SERA commité, pas celui du disque.

    Les deux diffèrent dès qu'un fichier est modifié après son `git add` — et
    c'est la version indexée qui partirait.
    """
    try:
        brut = subprocess.run(["git", "show", f":{chemin}"], capture_output=True,
                              timeout=120).stdout
    except Exception:  # noqa: BLE001
        return ""
    return brut.decode("utf-8", errors="ignore")


def main() -> int:
    fautes: list[str] = []
    for chemin in fichiers_indexes():
        if chemin_interdit(chemin):
            fautes.append(f"  {chemin} — fichier d'accès, jamais versionné")
            continue
        if chemin in EXCEPTIONS:
            continue
        for ligne, longueur in secrets_dans(contenu_indexe(chemin)):
            fautes.append(
                f"  {chemin}:{ligne} — secret de {longueur} caractères "
                f"(valeur non affichée)")

    if not fautes:
        return 0

    print("[pre-commit] COMMIT REFUSE — accès personnels", file=sys.stderr)
    for f in fautes:
        print(f, file=sys.stderr)
    print(
        "\n  Retirer de l'index :  git restore --staged <fichier>\n"
        "  Un secret doit vivre dans .env, qui n'est pas versionné.\n",
        file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
