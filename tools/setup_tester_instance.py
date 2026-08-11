"""Installe un terminal MT5 dédié au testeur, séparé de celui qui trade.

Pourquoi
--------
MT5 refuse deux instances issues du même dossier d'installation, et un
`.ini` de testeur porte `ShutdownTerminal=1` — indispensable pour que
l'appel rende la main, mais qui fermerait le terminal en cours. Lancer un
balayage sur l'installation principale reviendrait donc à couper le flux
de données et, si la boucle est armée, à abandonner des positions ouvertes
sans surveillance.

Une seconde installation en mode portable lève l'obstacle : elle a son
propre dossier de données, son propre cache d'historique, et peut tourner
en boucle des heures durant pendant que le terminal principal trade.

Ce qui est copié, et ce qui ne l'est pas
----------------------------------------
Le programme (~400 Mo) et la configuration des comptes (~1 Mo) sont
copiés. Le cache d'historique (30 Go) ne l'est PAS : l'instance le
retéléchargera à la demande. C'est plus lent au premier balayage et bien
plus sain — un cache recopié à froid contient des barres périmées que le
testeur rejouerait sans le dire.

    python tools/setup_tester_instance.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
CIBLE = Path.home() / "MT5Tester"

# Le programme, sans les dossiers de données de l'installation d'origine.
EXCLUS = {"Bases", "MQL5", "Tester", "Profiles", "logs"}


def source_programme() -> Path | None:
    for c in (
        Path(r"C:\Program Files\MetaTrader 5"),
        Path(r"C:\Program Files\Axi MT5 Terminal"),
    ):
        if (c / "terminal64.exe").exists():
            return c
    for base in (Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")):
        if base.exists():
            for d in base.glob("*/terminal64.exe"):
                return d.parent
    return None


def source_donnees() -> Path | None:
    base = Path.home() / "AppData/Roaming/MetaQuotes/Terminal"
    if not base.exists():
        return None
    cands = [d for d in base.iterdir() if (d / "config").is_dir()]
    if not cands:
        return None
    return max(cands, key=lambda d: d.stat().st_mtime)


def main() -> int:
    prog = source_programme()
    donnees = source_donnees()
    if prog is None:
        print("✗ installation MT5 introuvable")
        return 1
    print(f"source programme : {prog}")
    print(f"source config    : {donnees}")
    print(f"destination      : {CIBLE}")

    deja = (CIBLE / "terminal64.exe").exists()
    if deja:
        print("\ninstance déjà présente — mise à jour du seul EA")
    else:
        print("\n[1/4] copie du programme (~400 Mo)…")
        CIBLE.mkdir(parents=True, exist_ok=True)
        # robocopy gère les chemins longs et les fichiers verrouillés mieux
        # que shutil, et sait exclure des sous-arbres entiers.
        cmd = ["robocopy", str(prog), str(CIBLE), "/E", "/NFL", "/NDL",
               "/NJH", "/NJS", "/NP", "/R:1", "/W:1", "/XD"]
        cmd += [str(prog / d) for d in EXCLUS]
        subprocess.run(cmd, capture_output=True, check=False)
        if not (CIBLE / "terminal64.exe").exists():
            print("✗ copie incomplète")
            return 1
        print("      programme copié")

        # Le drapeau /portable fait vivre les données dans ce dossier.
        # Sans ces répertoires, le terminal recrée un dossier AppData et
        # l'isolement recherché disparaît sans prévenir.
        for d in ("config", "MQL5/Experts", "MQL5/Files", "MQL5/Profiles",
                  "Bases", "Tester", "logs"):
            (CIBLE / d).mkdir(parents=True, exist_ok=True)

        # La bibliothèque standard MQL5 (`Include\\Trade\\Trade.mqh` et le
        # reste) vit dans le dossier de DONNÉES, pas dans le programme.
        # Sans elle la compilation échoue sur un `error 106: file not found`
        # qui ne dit pas d'où vient le manque.
        if donnees and (donnees / "MQL5" / "Include").is_dir():
            print("[2/4] report de la bibliothèque standard MQL5…")
            subprocess.run(
                ["robocopy", str(donnees / "MQL5" / "Include"),
                 str(CIBLE / "MQL5" / "Include"), "/E", "/NFL", "/NDL",
                 "/NJH", "/NJS", "/NP", "/R:1", "/W:1"],
                capture_output=True, check=False)
            print("      bibliothèque reportée")

        if donnees and (donnees / "config").is_dir():
            print("      report de la configuration des comptes…")
            for f in (donnees / "config").glob("*.ini"):
                # `settings.ini` porte l'état de l'interface du terminal
                # principal (fenêtres, graphiques ouverts) : inutile ici et
                # source de démarrages lents.
                if f.name == "settings.ini":
                    continue
                shutil.copy2(f, CIBLE / "config" / f.name)

            # `servers.dat` est LA pièce indispensable : sans elle l'instance
            # ne connaît que les serveurs MetaQuotes intégrés, et une
            # connexion à un serveur de courtier échoue sans même produire
            # de ligne réseau — panne muette, difficile à diagnostiquer.
            # (`accounts.dat` est volontairement laissé : les mots de passe
            # y sont chiffrés par chemin d'installation, donc inutilisables
            # ici. C'est pourquoi le `.ini` du testeur les réinjecte.)
            for nom in ("servers.dat", "dnsperf.dat"):
                src_dat = donnees / "config" / nom
                if src_dat.exists():
                    shutil.copy2(src_dat, CIBLE / "config" / nom)
            if (donnees / "origin.txt").exists():
                shutil.copy2(donnees / "origin.txt", CIBLE / "origin.txt")
            print("      comptes et liste de serveurs reportés")

    print("[3/4] déploiement de l'EA de rejeu…")
    src = RACINE / "titanium" / "bridge" / "titanium_replay.mq5"
    shutil.copy2(src, CIBLE / "MQL5" / "Experts" / src.name)

    editeur = CIBLE / "metaeditor64.exe"
    if not editeur.exists():
        editeur = CIBLE / "MetaEditor64.exe"
    if editeur.exists():
        log = CIBLE / "compile.log"
        # `/portable` est indispensable : sans lui MetaEditor cherche la
        # bibliothèque standard dans le dossier AppData de CETTE copie —
        # qui vient d'être créé et qui est vide. Le message d'erreur parle
        # alors d'un `Trade.mqh` introuvable, alors qu'il est bien là.
        subprocess.run(
            [str(editeur), "/portable",
             f"/compile:{CIBLE / 'MQL5' / 'Experts' / src.name}",
             f"/log:{log}"],
            capture_output=True, timeout=180, check=False,
        )
        if log.exists():
            for ligne in log.read_text(encoding="utf-16",
                                       errors="replace").splitlines():
                if "error" in ligne.lower() or "Result" in ligne:
                    print(f"      {ligne.strip()}")
    ex5 = CIBLE / "MQL5" / "Experts" / "titanium_replay.ex5"
    if not ex5.exists():
        print("✗ EA non compilé dans l'instance de test")
        return 1

    print("[4/4] prêt.")
    print(f"\n  terminal de test : {CIBLE / 'terminal64.exe'}")
    print("  premier lancement : le terminal doit se connecter au compte")
    print("  et télécharger l'historique — compte quelques minutes.")
    print(f"\n  python tools/metatester.py --symbol XAUUSD --tester-instance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
