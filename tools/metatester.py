"""Balayage des paramètres de gestion au testeur natif MT5.

    python tools/metatester.py --symbol XAUUSD --rounds 2

Chaîne complète : V14 rejoue et produit ses signaux → l'EA les exécute sur
ticks réels → l'optimiseur balaie la gestion → les passes reviennent ici.

Sur « boucler jusqu'à trouver du rentable »
-------------------------------------------
C'est possible, et c'est aussi la façon la plus efficace de se mentir : en
cherchant assez longtemps on finit toujours par trouver un réglage flatteur
sur un échantillon donné. Trois garde-fous sont donc câblés en dur et non
débrayables :

1. Le mode « forward » de MT5 réserve le dernier tiers de la période. Un
   réglage n'est retenu que s'il tient AUSSI hors échantillon.
2. Le nombre de combinaisons essayées est compté sur toutes les rondes
   cumulées et figure dans chaque rapport. C'est lui qui dit ce que vaut
   un « meilleur résultat ».
3. Une passe sous 30 trades est annulée par `OnTester()` côté EA.

La boucle s'arrête quand un réglage passe ces filtres, ou quand la grille
est épuisée. Elle ne s'arrête jamais « parce qu'on a enfin trouvé ».
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.bridge.metatester import (  # noqa: E402
    NOM_EA, NOM_SIGNAUX, Plage, _epoch, combinaisons, ecrire_ini,
    exporter_signaux, lancer, lire_rapport, retenir,
)

SORTIE = RACINE / "results" / "metatester"


def compte_depuis_env() -> tuple[str, str, str] | None:
    """Identifiants du compte de test, lus dans `.env`.

    L'instance portable ne peut PAS hériter des mots de passe du terminal
    principal : MT5 les chiffre par chemin d'installation. Il faut donc
    soit les fournir ici, soit se connecter une fois à la main dans
    l'instance — après quoi elle les retient.
    """
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv(RACINE / ".env")
    except Exception:  # noqa: BLE001
        pass
    login = os.getenv("TITANIUM_DEMO_LOGIN", "").strip()
    mdp = os.getenv("TITANIUM_DEMO_PASSWORD", "").strip()
    serveur = os.getenv("TITANIUM_DEMO_SERVER", "").strip()
    if login and mdp and serveur:
        return login, mdp, serveur
    return None


# ── Les grilles sont ÉTAGÉES, pas croisées.
#
#    Le produit cartésien complet fait 5 400 combinaisons. Sur quelques
#    centaines de trades, cela revient à essayer une dizaine de réglages
#    par trade : on trouverait un gagnant même sur des données aléatoires,
#    et ce gagnant ne survivrait à rien.
#
#    Chaque étape répond donc à UNE question, fige sa réponse, et passe à
#    la suivante. Le coût tombe de 5 400 à 57 essais, et chaque chiffre
#    reste interprétable.
#
#    Ce n'est pas une recherche d'optimum global — c'en est délibérément
#    l'opposé. L'ordre des étapes est un choix qui influence le résultat,
#    et il suit l'ordre des questions posées, pas l'inverse.
ETAPES = [
    ("stop et objectif", [           # la thèse « le SL est trop court »
        Plage("InpSLmult", 1.0, 0.5, 3.0),      # 5
        Plage("InpRR", 1.5, 0.5, 4.0),          # 6
    ]),                                          # → 30
    ("stop temporel", [              # « le trade doit vite passer positif »
        Plage("InpStopBars", 0.0, 5.0, 20.0),   # 5
        Plage("InpStopMinR", 0.0, 0.25, 0.5),   # 3
    ]),                                          # → 15
    ("gestion en cours", [
        Plage("InpBEatR", 0.0, 0.5, 1.5),       # 4
        Plage("InpTrailStartR", 0.0, 1.0, 2.0), # 3
    ]),                                          # → 12
]

# Valeurs par défaut des paramètres non balayés à l'étape courante.
FIGES = {
    "InpRiskPct": 1.0,
    "InpSLmult": 1.0,
    "InpRR": 2.0,
    "InpBEatR": 0.0,
    "InpTrailStartR": 0.0,
    "InpTrailDistR": 1.0,
    "InpStopBars": 0.0,
    "InpStopMinR": 0.0,
    "InpMinTrades": 30.0,
}

# En deçà, un « meilleur réglage » relève du tirage au sort.
TRADES_PAR_COMBINAISON = 5.0


def grille_etape(i: int, acquis: dict[str, float]) -> list[Plage]:
    """Plages de l'étape `i`, les étapes précédentes étant figées."""
    _, balayees = ETAPES[i]
    noms = {p.nom for p in balayees}
    fixes = [Plage(n, acquis.get(n, v))
             for n, v in FIGES.items() if n not in noms]
    return balayees + fixes


INSTANCE_TEST = Path.home() / "MT5Tester"


BATTEMENT = RACINE / "results" / "loop_heartbeat.json"


def boucle_armee() -> float | None:
    """Âge du battement de la boucle live, en secondes, ou None.

    Le compte est unique : lancer le testeur ÉJECTE le terminal principal.
    Si la boucle trade à ce moment-là, elle perd sa source de prix en
    pleine position — et le gestionnaire de stop avec. On refuse donc.
    """
    import json
    import time
    if not BATTEMENT.exists():
        return None
    try:
        d = json.loads(BATTEMENT.read_text(encoding="utf-8"))
        ts = float(d.get("ts") or d.get("timestamp") or 0)
    except Exception:  # noqa: BLE001
        # Fichier illisible : on se rabat sur la date de modification,
        # plus prudent que de conclure « pas de boucle ».
        ts = BATTEMENT.stat().st_mtime
    age = time.time() - ts
    return age if age >= 0 else None


def terminal_en_cours() -> list[int]:
    """PID des terminaux MT5 actifs, hors instance de test.

    Le `.ini` du testeur porte `ShutdownTerminal=1` : lancer un balayage
    pendant que le terminal principal tourne le FERMERAIT, coupant le flux
    de données et laissant sans surveillance d'éventuelles positions.
    """
    import subprocess as sp
    try:
        out = sp.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process terminal64 -ErrorAction SilentlyContinue | "
             "ForEach-Object { \"$($_.Id)|$($_.Path)\" }"],
            capture_output=True, text=True, timeout=30, check=False).stdout
    except Exception:  # noqa: BLE001
        return []
    pids = []
    for ligne in out.splitlines():
        if "|" not in ligne:
            continue
        pid, chemin = ligne.split("|", 1)
        if str(INSTANCE_TEST).lower() in chemin.strip().lower():
            continue        # l'instance dédiée : sans conséquence
        try:
            pids.append(int(pid))
        except ValueError:
            pass
    return pids


def trouver_terminal() -> Path | None:
    for c in (
        Path(r"C:\Program Files\MetaTrader 5\terminal64.exe"),
        Path(r"C:\Program Files\Axi MT5 Terminal\terminal64.exe"),
        Path(r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"),
    ):
        if c.exists():
            return c
    for base in (Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")):
        if base.exists():
            for d in base.glob("*MT5*/terminal64.exe"):
                return d
            for d in base.glob("*MetaTrader*/terminal64.exe"):
                return d
    return None


def trouver_donnees() -> Path | None:
    """Le dossier de données du terminal, où vivent MQL5/Experts et Files."""
    base = Path.home() / "AppData/Roaming/MetaQuotes/Terminal"
    if not base.exists():
        return None
    cands = [d for d in base.iterdir() if (d / "MQL5" / "Experts").is_dir()]
    if not cands:
        return None
    # Le plus récemment utilisé : plusieurs terminaux peuvent coexister.
    return max(cands, key=lambda d: (d / "MQL5").stat().st_mtime)


def compiler(donnees: Path, portable: bool = False) -> bool:
    editeur = None
    for c in (
        Path(r"C:\Program Files\MetaTrader 5\metaeditor64.exe"),
        Path(r"C:\Program Files\Axi MT5 Terminal\metaeditor64.exe"),
    ):
        if c.exists():
            editeur = c
            break
    if portable:
        for n in ("MetaEditor64.exe", "metaeditor64.exe"):
            if (donnees / n).exists():
                editeur = donnees / n
                break
    if editeur is None:
        term = trouver_terminal()
        if term:
            c = term.parent / "metaeditor64.exe"
            editeur = c if c.exists() else None
    if editeur is None:
        print("  ! metaeditor64.exe introuvable — compile l'EA à la main")
        return False

    src = donnees / "MQL5" / "Experts" / f"{NOM_EA}.mq5"
    log = SORTIE / "compilation.log"
    cmd = [str(editeur)]
    if portable:
        # Sans /portable, MetaEditor cherche la bibliothèque standard dans
        # le dossier AppData de la copie — vide — et non dans la copie.
        cmd.append("/portable")
    cmd += [f"/compile:{src}", f"/log:{log}"]
    subprocess.run(cmd, capture_output=True, timeout=180, check=False)
    ex5 = src.with_suffix(".ex5")
    if log.exists():
        txt = log.read_text(encoding="utf-16", errors="replace")
        for ligne in txt.splitlines():
            if "error" in ligne.lower():
                print(f"    {ligne.strip()}")
    return ex5.exists()


def produire_signaux(symbol: str, barres: int, ltf: str, htf: str) -> list:
    """Fait tourner le rejeu V14 pour obtenir ses décisions."""
    import pandas as pd  # noqa: F401
    from titanium.backtest import rejouer
    from titanium.data.mt5_vendor import ensure_symbol, get_rates

    spec = ensure_symbol(symbol)
    cout = spec.spread * spec.point if spec else 0.0
    df_ltf = get_rates(symbol, ltf, barres)
    df_htf = get_rates(symbol, htf, max(300, barres // 16))
    res = rejouer(symbol, df_ltf, df_htf, spread=cout)
    return res.trades


def fermer_instance_test() -> int:
    """Ferme toute instance portable résiduelle.

    MT5 ne démarre pas deux fois depuis le même dossier : un second
    lancement passe la main à l'instance déjà ouverte et rend aussitôt la
    main. Le testeur ne tourne alors jamais, et l'échec se présente comme
    un « rapport introuvable » sans la moindre ligne de journal.
    """
    import subprocess as sp
    try:
        sp.run(["powershell", "-NoProfile", "-Command",
                "Get-Process terminal64 -ErrorAction SilentlyContinue | "
                f"Where-Object {{ $_.Path -like '*{INSTANCE_TEST.name}*' }} | "
                "Stop-Process -Force"],
               capture_output=True, timeout=60, check=False)
    except Exception:  # noqa: BLE001
        return 0
    return 1


def _diagnostic(donnees: Path) -> list[str]:
    """Relit le journal du terminal pour dire POURQUOI rien n'est sorti.

    Sans cela, l'échec le plus courant — instance sans compte — se présente
    comme un simple « rapport introuvable » qui n'oriente vers rien.
    """
    # `metaeditor.log` est souvent le plus récent et ne dit rien du testeur.
    logs = sorted((f for f in (donnees / "logs").glob("*.log")
                   if f.stem.isdigit()),
                  key=lambda f: f.stat().st_mtime, reverse=True)
    if not logs:
        return ["aucun journal terminal à relire"]
    try:
        txt = logs[0].read_text(encoding="utf-16", errors="replace")
    except Exception:  # noqa: BLE001
        return ["journal terminal illisible"]

    msgs = []
    for ligne in txt.splitlines():
        bas = ligne.lower()
        if "account is not specified" in bas:
            msgs.append("l'instance de test n'a aucun compte connecté.")
            msgs.append("→ renseigne TITANIUM_DEMO_PASSWORD et "
                        "TITANIUM_DEMO_SERVER dans .env,")
            msgs.append("   ou connecte-toi une fois à la main dans "
                        f"{donnees / 'terminal64.exe'}")
        elif "tester" in bas and ("error" in bas or "didn't start" in bas
                                  or "not started" in bas):
            msgs.append(ligne.strip()[-140:])
    return msgs[:6] or ["journal sans message d'erreur explicite"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="XAUUSD",
                    help="un symbole, ou plusieurs séparés par des virgules")
    ap.add_argument("--periode", default="M15")
    ap.add_argument("--barres", type=int, default=5000)
    ap.add_argument("--htf", default="H4")
    ap.add_argument("--rounds", type=int, default=3,
                    help="nombre d'étapes à parcourir (max 3)")
    ap.add_argument("--jours", type=int, default=365)
    ap.add_argument("--modele", type=int, default=4,
                    help="4 = ticks réels, 1 = OHLC M1 (plus rapide)")
    ap.add_argument("--timeout", type=int, default=7200)
    ap.add_argument("--dry-run", action="store_true",
                    help="prépare tout sans lancer le terminal")
    ap.add_argument("--tester-instance", action="store_true",
                    help="utilise le terminal dédié ~/MT5Tester (portable)")
    ap.add_argument("--signaux-seulement", action="store_true",
                    help="produit le fichier de signaux et s'arrête")
    a = ap.parse_args()

    SORTIE.mkdir(parents=True, exist_ok=True)

    if a.tester_instance:
        terminal = INSTANCE_TEST / "terminal64.exe"
        donnees = INSTANCE_TEST
        if not terminal.exists():
            print("✗ instance de test absente — lance d'abord :")
            print("    python tools/setup_tester_instance.py")
            return 1
    else:
        terminal = trouver_terminal()
        donnees = trouver_donnees()
    if terminal is None:
        print("✗ terminal64.exe introuvable")
        return 1
    if donnees is None:
        print("✗ dossier de données MT5 introuvable")
        return 1
    print(f"terminal : {terminal}")
    print(f"données  : {donnees.name}")

    # ── Garde-fou : jamais fermer le terminal qui trade.
    if not a.dry_run:
        occupes = terminal_en_cours()
        if occupes and not a.tester_instance:
            print(f"\n✗ terminal MT5 déjà en cours (pid {occupes[0]}).")
            print("  Le testeur le fermerait et couperait le flux de données.")
            print("  Deux issues :")
            print("    • python tools/setup_tester_instance.py   "
                  "(instance dédiée, à faire une fois)")
            print("      puis relancer avec --tester-instance")
            print("    • ou fermer MT5 et relancer")
            return 1

    symboles = [s.strip().upper() for s in a.symbol.split(",") if s.strip()]

    # ── Garde-fou : le compte est UNIQUE.
    #    Lancer le testeur éjecte le terminal principal — MT5 n'admet pas
    #    deux sessions sur un login. Si la boucle armée trade à cet instant,
    #    elle perd sa source de prix en pleine position, et le gestionnaire
    #    de stop avec elle. L'éjection est transitoire (le principal se
    #    reconnecte seul ensuite), mais pas inoffensive pendant.
    if not a.dry_run and not a.signaux_seulement:
        age = boucle_armee()
        if age is not None and age < 180:
            print(f"\n✗ la boucle live bat depuis {age:.0f} s — elle est armée.")
            print("  Le testeur éjecterait le terminal et la laisserait sans")
            print("  prix en pleine position. Arrête la boucle, puis relance.")
            print("  (ou --signaux-seulement, qui ne touche à rien)")
            return 1

    # ── PHASE 1 — toutes les décisions de V14, terminal principal connecté.
    #    Tout est produit AVANT le premier lancement du testeur : c'est ce
    #    qui permet de n'avoir qu'un seul compte. Le fichier de signaux
    #    porte tous les symboles et l'EA filtre sur le sien — le format
    #    était déjà prévu pour ça.
    print(f"\n[1/5] rejeu V14 — {len(symboles)} symbole(s), "
          f"{a.barres} barres {a.periode}")
    tous: list = []
    par_symbole: dict[str, list] = {}
    for sym in symboles:
        try:
            t = produire_signaux(sym, a.barres, a.periode, a.htf)
        except Exception as exc:                   # noqa: BLE001
            print(f"      {sym:8} ✗ {str(exc)[:70]}")
            continue
        par_symbole[sym] = t
        tous += t
        print(f"      {sym:8} {len(t)} signaux")

    if not tous:
        print("✗ aucune décision produite — rien à optimiser")
        return 1

    cible = donnees / "MQL5" / "Files" / NOM_SIGNAUX
    n_total = exporter_signaux(tous, cible)
    exporter_signaux(tous, SORTIE / NOM_SIGNAUX)   # copie traçable
    print(f"      {n_total} signaux au total → {cible}")

    if a.signaux_seulement:
        print("\n[signaux seulement] terminé — le terminal principal reste")
        print("  connecté. Relance sans ce drapeau pour lancer le testeur.")
        return 0

    # ── 2. Déploiement + compilation de l'EA.
    print("\n[2/5] compilation de l'EA…")
    src = RACINE / "titanium" / "bridge" / f"{NOM_EA}.mq5"
    shutil.copy2(src, donnees / "MQL5" / "Experts" / f"{NOM_EA}.mq5")
    if not compiler(donnees, portable=a.tester_instance):
        print("✗ compilation échouée")
        return 1
    print("      compilé")

    # ── PHASE 2 — le testeur. À partir d'ici le terminal principal est
    #    éjecté ; plus rien ne doit avoir besoin de lui.
    print(f"\n[3/5] testeur — {len(par_symbole)} symbole(s) à la suite")
    resultats: dict[str, list] = {}
    for sym, trades in par_symbole.items():
        print(f"\n{'═' * 20} {sym} {'═' * 20}")
        resultats[sym] = _optimiser(
            sym, trades, a, terminal=terminal, donnees=donnees)

    journal = SORTIE / "rondes.json"
    journal.write_text(json.dumps(resultats, indent=2, default=str),
                       encoding="utf-8")
    print(f"\njournal → {journal}")
    print("\nle terminal principal se reconnecte de lui-même une fois le")
    print("testeur refermé — vérifie avant de réarmer la boucle.")
    return 0


def _optimiser(symbole: str, trades: list, a, *, terminal: Path,
               donnees: Path) -> list:
    """Parcourt les étapes pour un symbole. Rend l'historique des étapes."""
    n = len(trades)

    # La fenêtre du testeur suit les signaux, pas le calendrier : une
    # fenêtre plus large ferait tourner le testeur à vide sur la part non
    # couverte, et diluerait les statistiques sans le dire.
    epochs = sorted(e for e in (_epoch(t.bar_entree) for t in trades) if e)
    if not epochs:
        print("      ✗ aucun horodatage exploitable")
        return []
    debut = datetime.fromtimestamp(epochs[0], timezone.utc).date() \
        - timedelta(days=2)
    fin = datetime.fromtimestamp(epochs[-1], timezone.utc).date() \
        + timedelta(days=2)
    print(f"      {n} signaux · fenêtre {debut} → {fin} "
          f"({(fin - debut).days} j)")
    if n < 30:
        print(f"  ! {n} signaux : sous le seuil de 30, toute passe sera nulle")

    total_essais = 0
    historique: list = []
    acquis: dict[str, float] = {}
    etapes = ETAPES[:a.rounds] if a.rounds <= len(ETAPES) else ETAPES

    for ronde, (titre, _) in enumerate(etapes, start=1):
        plages = grille_etape(ronde - 1, acquis)
        nb = combinaisons(plages)
        total_essais += nb
        ratio = n / max(1, nb)
        print(f"\n[étape {ronde}/{len(etapes)}] {titre} — {nb} combinaisons "
              f"({total_essais} cumulées)")
        for p in plages:
            if p.pas > 0:
                print(f"      {p.nom:16s} {p.depart} → {p.fin} pas {p.pas}")
        if acquis:
            print(f"      figé : "
                  + ", ".join(f"{k}={v}" for k, v in sorted(acquis.items())))
        print(f"      {ratio:.1f} signaux par combinaison", end="")
        if ratio < TRADES_PAR_COMBINAISON:
            print(f"  ← SOUS {TRADES_PAR_COMBINAISON:.0f}, résultat non"
                  " concluant quelle que soit la sortie")
        else:
            print()

        rapport = f"titanium_{symbole}_e{ronde}"
        ini = ecrire_ini(
            SORTIE / f"{rapport}.ini",
            symbol=symbole, periode=a.periode,
            depuis=debut.strftime("%Y.%m.%d"), jusqua=fin.strftime("%Y.%m.%d"),
            plages=plages, rapport=rapport, modele=a.modele,
            compte=compte_depuis_env() if a.tester_instance else None,
        )
        if a.dry_run:
            print(f"      [dry-run] {ini}")
            break

        if a.tester_instance:
            fermer_instance_test()
        print("      testeur en cours (ticks réels, cela peut être long)…")
        lancer(terminal, ini, timeout=a.timeout,
               portable=a.tester_instance)

        # MT5 écrit le rapport à côté du terminal, pas du .ini.
        xml = None
        for c in (donnees / f"{rapport}.xml",
                  donnees / "Tester" / f"{rapport}.xml",
                  terminal.parent / f"{rapport}.xml",
                  donnees / "MQL5" / "Files" / f"{rapport}.xml"):
            if c.exists():
                xml = c
                break
        if xml is None:
            print("      ✗ rapport introuvable")
            for m in _diagnostic(donnees):
                print(f"        {m}")
            break

        passes = lire_rapport(xml)
        bons = retenir(passes)
        print(f"      {len(passes)} passes lues, {len(bons)} viables "
              f"(≥30 trades, PF ≥ 1.2, espérance > 0)")

        historique.append({
            "etape": ronde, "titre": titre, "combinaisons": nb,
            "cumul": total_essais, "signaux_par_combinaison": round(ratio, 2),
            "passes": len(passes), "viables": len(bons),
            "meilleur": bons[0].__dict__ if bons else None,
        })

        if not bons:
            print("      aucun réglage viable à cette étape — on garde les"
                  " valeurs par défaut et on passe à la suivante")
            continue

        m = bons[0]
        print(f"      meilleur : espérance {m.esperance_r:+.3f} R, "
              f"PF {m.profit_factor:.2f}, {m.trades} trades")
        for k, v in sorted(m.params.items()):
            print(f"        {k} = {v}")

        # On fige ce que cette étape a tranché, pour les suivantes.
        for k, v in m.params.items():
            if k in FIGES:
                acquis[k] = v

    if total_essais:
        print(f"      combinaisons essayées : {total_essais}"
              "  ← à citer dans toute conclusion")
    return historique




if __name__ == "__main__":
    raise SystemExit(main())
