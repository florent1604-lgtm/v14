"""Rejoue en Python la logique de lecture de `titanium_zones.mq5`.

    python tools/verifier_zones_mql5.py

Pourquoi cet outil
------------------
Quand un graphique MT5 n'affiche rien, trois causes sont indiscernables de
l'extérieur : le fichier n'est pas écrit, il est écrit au mauvais endroit,
ou l'indicateur ne sait pas le découper. Les trois se présentent comme un
écran vide, sans message.

Ce script reproduit **exactement** la chaîne MQL5 — lecture en FILE_TXT
(qui retire les fins de ligne), découpe par marqueur, extraction naïve par
sous-chaînes — et dit lequel des trois cas s'applique, sans avoir à ouvrir
un graphique.
"""

from __future__ import annotations

import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.bridge.mt5_zones import (  # noqa: E402
    MARQUEUR, NOM_FICHIER, dossier_mql5_files,
)


def lire_comme_mql5(chemin: Path) -> str:
    """`FileReadString` en FILE_TXT rend les lignes SANS leur fin de ligne.

    C'est le détail qui a fait échouer la première version : un marqueur
    contenant un saut de ligne n'était jamais retrouvé après lecture.
    """
    brut = chemin.read_text(encoding="utf-8", errors="replace")
    return "".join(brut.splitlines())


def extraire_bloc(js: str, symbole: str) -> str:
    """Transcription littérale de `ExtraireBloc` côté MQL5."""
    marque = f'"symbol": "{symbole}"'
    p = js.find(marque)
    if p < 0:
        marque = f'"symbol":"{symbole}"'
        p = js.find(marque)
        if p < 0:
            return ""

    deb = 0
    s = js.find(MARQUEUR)
    while s >= 0 and s < p:
        deb = s + len(MARQUEUR)
        s = js.find(MARQUEUR, s + 1)

    fin = js.find(MARQUEUR, p)
    if fin < 0:
        fin = len(js)
    return js[deb:fin]


def lire_texte(js: str, cle: str) -> str:
    """Transcription de `LireTexte` — extraction naïve par sous-chaînes."""
    p = js.find(f'"{cle}"')
    if p < 0:
        return ""
    d = js.find(":", p)
    if d < 0:
        return ""
    g1 = js.find('"', d)
    if g1 < 0:
        return ""
    g2 = js.find('"', g1 + 1)
    return js[g1 + 1:g2] if g2 > 0 else ""


def compter_zones(bloc: str) -> int:
    """Transcription de la boucle de `TracerZones`."""
    pos = bloc.find('"zones"')
    if pos < 0:
        return 0
    n, curseur = 0, pos
    while True:
        deb = bloc.find("{", curseur)
        if deb < 0:
            break
        fin = bloc.find("}", deb)
        if fin < 0:
            break
        morceau = bloc[deb:fin + 1]
        curseur = fin + 1
        if not lire_texte(morceau, "kind"):
            break              # sortie du tableau "zones"
        n += 1
    return n


def main() -> int:
    dossier = dossier_mql5_files()
    if dossier is None:
        print("✗ dossier MQL5/Files introuvable — MT5 n'est pas joignable")
        return 1
    print(f"dossier : {dossier}")

    f = dossier / NOM_FICHIER
    if not f.exists():
        print(f"✗ {NOM_FICHIER} absent → la boucle n'écrit pas, ou elle écrit")
        print("  ailleurs. Vérifie que `tools/live_demo.py` tourne.")
        return 1

    brut = f.read_text(encoding="utf-8", errors="replace")
    js = lire_comme_mql5(f)
    print(f"fichier : {len(brut)} octets sur disque, "
          f"{len(js)} après lecture MQL5 (fins de ligne retirées)")

    n_marqueurs = js.count(MARQUEUR)
    print(f"marqueurs retrouvés après lecture : {n_marqueurs}")
    if n_marqueurs == 0 and len(brut) > 4000:
        print("✗ AUCUN marqueur survivant : chaque graphique recevrait le")
        print("  fichier entier et dessinerait les zones du PREMIER bloc.")
        return 1

    print()
    ok = dessinent = 0
    for bloc_brut in brut.split(f"\n{MARQUEUR}\n"):
        sym = lire_texte(bloc_brut, "symbol")
        if not sym:
            continue
        ok += 1
        bloc = extraire_bloc(js, sym)
        if not bloc:
            print(f"  ✗ {sym:8} bloc introuvable après découpe")
            continue
        # Le symbole extrait doit être CELUI qu'on cherchait : si la découpe
        # rate, on récupère le bloc du voisin sans que rien ne le signale.
        trouve = lire_texte(bloc, "symbol")
        if trouve != sym:
            print(f"  ✗ {sym:8} découpe erronée → bloc de {trouve}")
            continue
        z = compter_zones(bloc)
        v = lire_texte(bloc, "verdict")
        plan = '"plan"' in bloc
        if z or plan:
            dessinent += 1
        print(f"  ✓ {sym:8} {v:6} · {z} zone(s) · plan {'oui' if plan else 'non'}"
              f" · bloc {len(bloc)} o")

    print()
    print(f"{ok} bloc(s) dans le fichier, {dessinent} dessineraient quelque chose.")
    if dessinent < ok:
        print("⚠️  Certains blocs n'ont ni zone ni plan — normal pour un")
        print("   symbole sans setup, anormal pour tous à la fois.")
    return 0 if dessinent else 1


if __name__ == "__main__":
    raise SystemExit(main())
