# Dossier de mise en production — Titanium V14

**Date :** 13 septembre 2026
**Périmètre :** évolution, techniques, outils et axes d'amélioration à intégrer, depuis l'état
réellement mesuré du dépôt.
**Documents liés :** `ARCHITECTURE_EXECUTION_MULTI_OS_V14_20260913.md` (pont MT5, Windows/macOS,
déploiement), `CORTEX_INGENIEUR_V14_20260913.md` (cortex Claude/Codex, pépinière d'agents) et
`VEILLE_RECHERCHE_ALPHA_V14_20260913.md` (sourcing d'alpha, sources de recherche, thèmes
testables).

> Méthode : tout chiffre de ce dossier a été lu dans un artefact du dépôt ou mesuré sur le
> terminal MT5 le 13/09/2026. Les affirmations issues de sources externes portent leur URL.
> Ce qui n'a pas été vérifié est dit « non vérifié ».

---

## 1. État des lieux mesuré

### 1.1 Ce qui tourne réellement

| Brique | État mesuré |
|---|---|
| Boucle de trading | `running` + `armed`, battement frais (2,8 s), intervalle **10 s**, compte DEMO **10055401** `Axi-US50-Demo` |
| Tableau de bord Windows | `http://127.0.0.1:8095` → HTTP 200, lancé par `desktop/` ou `tools/dashboard.py` |
| Graphe k3s | `titanium-api-…` **1/1 Running, 0 redémarrage**, ~3 mCPU / 36 MiB, sonde HTTP validée |
| Pont MT5 | **fichier** : `titanium/bridge/mt5_zones.py` écrit dans `MQL5/Files`, lu par `titanium_zones.mq5` |
| Cortex (Hermès) | 3 fournisseurs : `hermes-cli` (abonnement), `ollama-local`, `deepseek-api` ; disjoncteur + lotissement |
| Macro | flux externe + jauges livrés ; `PolicyContext.macro` additif, défaut `None` |
| Carnet L2 Binance | **20 Go** enregistrés (BTCUSDT, ETHUSDT, 52 fichiers depuis le 16/08) — **aucune feature ne le lit** |
| Volatilité | `VIX.fs` est le **seul** instrument de volatilité du catalogue (149 symboles) ; aucun contrat d'option |
| Famille adaptative | **17** comportements distincts, **10** gagnants distincts, matrice des 15 politiques **bit-à-bit** inchangée (seed 7) |
| MT5 dans k3s | adaptateur à **0 réplique** — le pont Windows natif n'est pas construit |
| Ordres envoyés | **0** |

### 1.2 Le tunnel de décision, chiffré (cumul sur 136 tours)

```
catalogue évalué      20 264
  écartés illiquides   8 704
  sélectionnés         4 216
  portables            1 496        non portables   2 720
                                      dont MARCHE_FERME  272
                                      dont COUT_SPREAD 2 448
gate_verdict           ENTER 2 662   BLOCK 6 097   WAIT 217
post_enter_refusal     COUT_SPREAD 2 254 (84,7 %)  MULTIPOSITION 191
                       EXECUTION 135 (TRACE_DUPLICATE)  DERIVE 81  MICROSTRUCTURE 1
envoyés                0
```

### 1.3 Le compte, à l'instant du relevé

- équité **1 864 EUR**, solde **1 905 EUR** → flottant **−40 EUR**.
- Ce flottant est dominé par **une seule position** : `DAX40.fs #108485347`, **+6,38 de brut contre
  −45,25 de portage** (swap). Le panneau l'affiche désormais honnêtement (`net`, `net_total`).
- **3 positions** V14, toutes magic 14000 (périmètre vérifié).
- Budget de risque : **17,1 %** global, **5,7 %** par grappe, invariant `3 × 5,7 = 17,1` tenu par un test.
  Occupation réelle : **2,2 %** → **12,9 %** du budget. Le système est très loin de sa limite de risque.

### 1.4 Le refus pour coût est justifié — mesuré, pas supposé

Le mur à 84,7 % a été décomposé terme par terme contre MT5 :

| Terme | Code | MT5 | Verdict |
|---|---|---|---|
| Spread | `spec.spread × spec.point` | `ask − bid` du tick | identique sur 19 symboles |
| Source | `ensure_symbol` | relu à chaque appel, non caché | pas de péremption |
| Dénominateur | `1,5 × ATR` (RiskGate) | `_trace['atr']` == `compute_atr` | même unité, même définition |
| Unité du plafond | `spread / stop` (fraction) | ratio en argent via `tick_value/tick_size` | **égal** au ratio en prix : seuil libre de lot et de devise |
| Commission / frais | non modélisés | **0** sur 70 deals sur 7 jours | sans objet : le spread est le seul coût |
| Aller-retour | 1 spread complet | `MODE_ENTREE = "MARCHE"` | exact au mode d'entrée |

Distribution réelle du coût (médiane / p90) : `LNKUSD 0,88/0,88` · `BCHUSD 2,08/1,37` ·
`BNB-USD 0,26/0,26` · `BTCUSD 0,24/0,17` · `XLMUSD 0,22/0,22` · `AAVE-USD 0,21/0,21` ·
`ETHUSD 0,17/0,17` · **`EURUSD 0,15/0,18`** · `XAUUSD 0,30/0,03` · `GER40 0,04/0,07` ·
**`US500 0,02/0,02`**. Et la portabilité : **18 symboles** refusés pour coût, médiane **27,5 %**,
p90 **62 %**, extrême `SAND-USD` **1590 %** (bid 0,023 / ask 0,048 : un spread à **108 % du prix**).

**Conclusion :** le plafond de 12,5 % fait son travail. Il n'est pas la bonne cible.
Le laisser tel quel est un **choix**, et ce dossier ne le déplace pas.

---

## 2. Le diagnostic honnête : trois goulots, par impact réel

### Goulot n°1 — la promotion d'un actif est liée à son coût, pas à sa qualité de signal

1 496 actifs portables sur 4 216 sélectionnés : le système écarte d'abord sur le prix du transport.
C'est sain. Mais ensuite, sur **2 662** signaux `ENTER`, **2 254** meurent au même endroit et **191**
sur la multi-position, **81** sur la dérive, **135** sur l'idempotence. Autrement dit : le système
**produit 2 662 setups par campagne et n'en exécute aucun**. Ce n'est pas un problème de données,
c'est un problème de **règle d'admission** — et le fait que le budget de risque soit occupé à
12,9 % pendant que zéro ordre part est la preuve que **la contrainte n'est pas le risque**.

**À vérifier avant tout autre chantier :** sur les quelques candidats qui passent la porte de coût
(à H4, `ETHUSD` et `BNB-USD` passent : 2,5 % et 7,7 %), quel étage refuse exactement, et dans quelles
proportions. C'est la seule mesure qui désigne le levier. Tant qu'elle n'est pas faite, tout le reste
de ce dossier est une hypothèse.

### Goulot n°2 — la latence n'est pas la contrainte, et la reconstruire d'abord serait une erreur

La cadence de décision est de **10 s**. Le coût d'un aller-retour de transport est de l'ordre de la
**milliseconde** (IPC MT5 Python) à la dizaine de microsecondes (socket loopback). Optimiser le
transport de 1 ms à 0,05 ms gagne **0,01 %** du cycle. Le chantier ZMQ/TCP que vous décrivez est
**juste dans son principe et faux dans sa priorité** : il ne fera pas entrer le premier ordre.
Il se justifie pour trois autres raisons, réelles — voir doc 2 : supprimer la dépendance à `wmic`/COM
sur le chemin chaud, rendre l'affichage MT5 poussé au lieu d'être relu, et permettre à terme un
exécuteur MQL5 capable de gérer les stop/trailing sans repasser par Python.

### Goulot n°3 — la visibilité du risque

Le portage de −45 EUR était **invisible** sur la carte pendant que les budgets passaient de 2 % à
17,1 %. Corrigé et livré (`net`, `net_total`, ligne « dont −45,25 de portage »), mais la leçon
générale demeure : **une surface de surveillance qui peut se tromper de 45 EUR pendant qu'on
multiplie le risque par 2,85 est un risque opérationnel, pas un détail d'interface.**

---

## 3. Ce que les acteurs institutionnels exécutent, et ce qui est transposable ici

Les algos d'exécution institutionnels se répartissent en familles stables
(sources : [MQL5/industry surveys](https://www.deepcharts.com/blog/vwap-volume-weighted-average-price-trading),
[CoinRoutes academy](https://coinroutes.com/academy/crypto-execution-algorithms-twap-vwap-pov-is/),
[BestEx Research sur l'IS](https://www.bestexresearch.com/insights/is-zero-reinventing-vwap-algorithms-to-minimize-implementation-shortfall)) :
**TWAP**, **VWAP**, **POV/Volume-in-line**, **Implementation Shortfall (IS / risk-averse)**, **SOR**
(smart order routing), **SWEEP**, **Iceberg / réservation**, plus les couches d'**anti-gaming**
(détection de prédation, randomisation de l'enfance, pas de motif d'horloge).

Ce qui se transpose à un compte DEMO à 1 865 EUR, et ce qui ne se transpose pas :

| Technique institutionnelle | Transposable ? | Pourquoi | Où, dans V14 |
|---|---|---|---|
| **IS / risk-averse** (arbitrer impact contre risque de dérive) | **Oui, directement** | C'est exactement le métier des 17 techniques adaptatives : choisir l'échelle et la vitesse selon l'urgence | `titanium/adaptive*`, sélecteur |
| **VWAP / TWAP** | **Oui, comme référence de calibration** | Utile pour juger une exécution *a posteriori*, pas pour entrer sur un signal discrétionnaire | nouvelle brique d'attribution |
| **POV / % du volume** | **Oui** | Sur crypto et indices, plafonner sa participation protège du signalement | règle de participation maximale |
| **Iceberg / ordres réservés** | Partiellement | MT5 retail : `volume_initial` sur ordre limite suffit, pas de carnet caché | limite de taille par tranche |
| **Anti-gaming** (randomisation, pas d'horloge) | **Oui, et peu coûteux** | Un bot qui poste à la seconde fixe depuis 136 tours est lisible | jitter sur l'intervalle et sur les tailles |
| **SOR / dark pools** | **Non** | Pas d'accès multi-venues sur ce compte | — |
| **Queue position / adverse selection fines** | **Non** | Exige la donnée MBO, indisponible en L1/L2 retail | — |

**Ce qui manque concrètement à V14**, dans l'ordre de valeur : (1) une **mesure d'attribution
d'exécution** (coût réalisé vs référence VWAP/TWAP de la période) pour savoir si l'entrée est bonne ;
(2) une **règle de participation maximale** (POV) ; (3) un **jitter déterministe** sur l'intervalle
et les tailles ; (4) une **détection de signalement** (le même symbole refusé 891 fois en une heure,
comme `LNKUSD`, est une signature lisible).

**Le volet amont — où naît une idée et par quelle porte elle entre — est traité dans
`VEILLE_RECHERCHE_ALPHA_V14_20260913.md`.** Son résultat principal : le gisement d'alpha le plus
proche n'est pas un papier, c'est le **carnet L2 de 20 Go déjà enregistré** (`results/carnet_binance`),
qui n'alimente aujourd'hui aucune feature et permettrait de **valider ou invalider** le modèle de
coût (spread fixe, impact nul) sur lequel repose la porte des 12,5 %. Et son verdict tranché :
**GEX, 0DTE et options sont hors de portée** sur ce compte — aucun contrat d'option n'est exposé, et
le resterait sans changer de courtier. Le backlog qui en découle, nommé et falsifiable, est dans
`CATALOGUE_HYPOTHESES_ALPHA_V14_20260913.md` — dont la première entrée (H1, impact linéaire en
déséquilibre du flux) est précisément la mesure qui teste le modèle de coût de la porte des 12,5 %.

---

## 4. Les outils à intégrer

| Outil | Pourquoi | Coût d'entrée |
|---|---|---|
| **MQL5 natif `Socket*`** (pas ZeroMQ) | Aucun DLL à embarquer ; `SocketCreate/Connect/Send/Read` sont dans la bibliothèque standard | faible — un EA, pas de dépendance native |
| **`codex exec --json --output-schema`** | Sortie **structurée conforme à un JSON Schema** : le cortex publie un verdict borné au lieu d'un texte à parser | faible |
| **`claude -p`** (sans `--bare`) | Raisonnement long sur l'abonnement Pro, sans facturation API | faible |
| **`psutil` + battement** | Détection de vivacité sans `wmic` (déjà en place) | nul |
| **OpenTelemetry ou Prometheus déjà présent** | Le serveur de métriques existe ; le tunnel n'y est pas publié | faible |
| **`electron-builder` cibles `mac`** | Le shell desktop existe mais **ne construit que pour Windows** | faible |
| **Signature + notarisation macOS** | Sans elles, macOS refuse l'app (Gatekeeper) | **élevé** (compte Apple Developer, 99 $/an) |
| **PyInstaller / backend figé** | Aujourd'hui l'app dépend d'un `.venv` à un chemin codé en dur | moyen |

**À ne pas intégrer :** ZeroMQ (il faut compiler et embarquer `libzmq` côté EA ; le bénéfice sur un
loopback est nul face à `SocketSend`), un second bus d'événements, un carnet d'ordres propriétaire.

---

## 5. Axes d'amélioration, priorisés

### P0 — avant toute nouvelle brique

1. **Attribuer le refus final, chiffré.** Rejouer le tunnel sur les candidats H4 qui passent la porte
   de coût et compter chaque étage. *Critère de sortie :* un tableau « étage → nombre → cause » où
   la somme des refus égale le nombre d'entrées candidates. *Mesure :* le premier levier nommé.
2. **Un seul propriétaire par règle de décision.** Fait pour le coût (commit `c60ba87`) :
   identité prouvée sur 596 cas gelés, 0 écart. Reste à faire pour `selection._cout_relatif` et
   `backtest.cout_spread_r`, **deux autres copies de la même arithmétique** — à réunifier en prouvant
   que les mesures scellées ne bougent pas.
3. **Corpus scellé : décider.** 6 sources moteur divergent déjà des 147 artefacts. Recalculer
   (~15 h) ou acter. **Décision utilisateur.**

### P1 — valeur opérationnelle directe

4. **Attribution d'exécution** : `cout_spread` refusé tracé numériquement partout (fait pour la
   portabilité), puis espérance réelle des trades clos **par décile de coût** → dir à la calibration
   du plafond, sans le déplacer.
5. **Règle de participation et jitter** : plafond de participation par symbole, intervalle et tailles
   randomisées de façon reproductible (graine fixe par tour).
6. **Détection de signalement** : alerter quand un symbole cumule plus de N refus par heure, ou quand
   la même intention revient à chaque balayage.
7. **Surface de surveillance complète** : publier le tunnel dans les métriques, pas seulement dans
   `loop_heartbeat.json`.

### P2 — capacité et industrialisation

8. **Cortex ingénieur** à deux bassins (doc 3) : agents d'analyse, d'audit et de calibration.
9. **Pont socket** (doc 2) et **desktop Windows + macOS**.
10. **Agrégateur de volatilité multi-venues** — demandé au départ, jamais construit. *Vérification :*
    aucune trace dans `docs/` (la seule occurrence d'« agrégateur » concerne le choix de la source la
    plus fraîche dans le flux macro, `CORRECTIONS_AUDIT_V14_20260907.md:47`). À déclarer
    explicitement comme non construit, ou à construire — mais pas à laisser implicite. Périmètre
    minimal honnête et porte de sortie : doc 4, §6 R3.
11. **Exploiter le carnet L2 (20 Go, zéro lecteur)** — doc 4, §6 R1. Coût marginal quasi nul,
    donnée déjà payée : déséquilibre du carnet, flux agressif signé, élasticité de profondeur.
    C'est la mesure qui teste le modèle de coût actuel.

---

## 6. Plan de mise en production, cinq phases

| Phase | Contenu | Porte de sortie |
|---|---|---|
| **0 — Vérité** | P0.1 (attribution chiffrée), P0.3 (décision corpus) | un levier nommé, une décision écrite |
| **1 — Socle** | P0.2 (propriétaire unique partout), suite + lint + CI verts | identité prouvée sur entrée gelée |
| **2 — Mesure** | P1.4 à P1.7, plus **R1** (carnet L2 → features, doc 4) | espérance par décile de coût, tunnel publié, coût modélisé confronté au coût réalisable |
| **3 — Exécution** | doc 2 : pont socket, desktop Win+mac, EA | latence p95 mesurée, `< 5 ms` bout en bout, repli fichier intact |
| **4 — Cortex** | doc 3 : deux bassins, contrat JSON, 4 agents | verdicts structurés, disjoncteur par fournisseur, **jamais** sur le chemin de l'ordre |
| **5 — Production** | réarmement, surveillance longue, promotion de seuils | **validation humaine explicite à chaque seuil** |

**Règle de sécurité qui traverse tout le dossier :** le cortex reste **hors du chemin critique de
l'ordre**. Aujourd'hui c'est déjà le cas — la boucle appelle `run_once` avec
`OrchestratorConfig(require_edge=False, deliberate=False, execute=False)`. Le LLM délibère, il ne
signe pas. Cette propriété doit survivre à toutes les phases.

---

## 7. Ce que ce dossier ne recommande pas

- **Déplacer le moteur dans WSL2 pour « plus de performance »** : la lecture des prix passerait de
  l'IPC MT5 à un saut réseau virtuel de 0,5 à 2 ms, pour aller chercher des gains CPU qui n'existent
  pas dans un cycle de 10 s. Le partage correct est ailleurs (doc 2).
- **Déplacer le plafond de coût.** Il est mesuré juste ; le bouger serait déplacer le thermomètre.
- **Introduire ZeroMQ.** Une dépendance native à embarquer dans un EA, pour zéro gain mesurable sur
  un loopback.
- **Mettre un LLM sur le chemin de l'ordre.** C'est la manière la plus rapide de perdre le
  déterminisme, et le refus fail-closed est ce qui protège ce compte.
