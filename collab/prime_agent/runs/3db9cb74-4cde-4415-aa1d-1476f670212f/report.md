# Point 3 — archives de marché L1 Axi et L2 Binance

Prime Agent · 16/08/2026 · tâche `3db9cb74-4cde-4415-aa1d-1476f670212f`

## Objectif

Les six politiques d'exécution dépendantes d'un carnet (`pegged`, `iceberg`,
`post_only`, `cancel_replace`, `market_making`, et en partie `adaptive`)
plafonnent à 0,35–0,50 de fidélité parce que le simulateur invente le carnet.
Le blocage n'est pas le moteur, c'est la donnée. Ce lot crée la donnée.

## État trouvé, après correction d'une erreur d'audit

Mon audit du matin affirmait qu'aucun archiveur n'existait. **C'était faux pour
la moitié MT5** : un premier `rglob` avait rendu zéro résultat, `git grep` a
donné l'inverse. `tools/enregistreur_quotes.py` avait été livré la veille au
soir (`523160f`). L'erratum est dans
`collab/prime_agent/runs/audit-rapport-kimi-20260815/report.md`.

Vérifié par une seconde méthode :

- L1 Axi : **existait**, 236 lignes, 8 tests — mais **était à l'arrêt** depuis le
  15/08 18:09 UTC. Dernier tick archivé : 15/08 18:09:27Z.
- L2 Binance : **absent**. `git grep -e orderbook_ws -e aggTrade -e depth@ -- *.py`
  ne rend rien.

## Livré

| Fichier | Nature |
|---|---|
| `tools/enregistreur_carnet_binance.py` | collecteur L2 + transactions, vérification, compaction |
| `tests/test_enregistreur_carnet_binance.py` | 33 tests |
| `tools/etat_services.py` | suivi des collecteurs, séparé des services |
| `tests/test_etat_services_collecteurs.py` | 6 tests |
| `COLLECTE_V14.bat` | lancement des deux archives |
| `docs/ARCHIVE_MARCHE_V14.md` | format, commandes, volumes, limites |

### Décisions de conception

1. **On archive le brut.** Instantané d'amorce puis différentiels tels que
   Binance les émet. Un « top 20 » agrégé jetterait de l'information qu'aucun
   retraitement ne récupère — l'erreur exacte que ce lot corrige.
2. **Prix et quantités restent des chaînes.** Un `float` à l'écriture figerait
   un arrondi définitif dans une archive censée faire autorité.
3. **Tout différentiel reçu est écrit.** La séquence annote les ruptures, elle
   ne filtre pas : un collecteur qui décide de jeter une ligne peut jeter la
   mauvaise, et la perte est muette.
4. **L'instantané est demandé après le premier différentiel reçu**, jamais avant.
   L'ordre inverse laisse un trou de la durée de la connexion : mesuré à deux
   trous d'amorce au premier essai, zéro après correction.
5. **Hôtes `binance.vision`** — points d'accès données de marché, sans clé et
   sans aucun point d'entrée de trading. L'absence d'ordre devient structurelle,
   pas déclarative ; un test AST le verrouille.

### Deux défauts trouvés par mes propres contrôles

**Le vérificateur a refusé BTCUSDT.** Un redémarrage du collecteur avait laissé
273 mises à jour manquantes, et la première version du contrôle enjambait le
trou : elle rendait un carnet plausible et faux. Corrigé par la notion de
**session** — un rejeu ne franchit jamais un instantané `amorce` ou `trou`.
Test : `test_un_redemarrage_ouvre_une_session_et_le_rejeu_ne_la_franchit_pas`.

**Une seconde boucle armée, évitée de justesse.** Ma première version comptait
un doublon de collecteur comme une panne, donc `tools/etat_services.py` rendait
1. Or `DEMARRER_V14.bat` s'arrête quand ce script rend **0** : un archiveur en
double aurait fait croire au `.bat` que V14 était à l'arrêt, et lancé une
seconde boucle armée sur le même compte — l'incident du 08/08/2026. Le code de
sortie reste la propriété des trois services. Test :
`test_un_collecteur_en_double_est_signale_sans_toucher_au_code_de_sortie`.

## Preuves

```
.venv\Scripts\python.exe -m pytest -q
1920 passed, 2 skipped, 69 subtests passed in 122.78s
```

Reconstruction sur données réelles, 30 minutes de collecte :

```
.venv\Scripts\python.exe tools\enregistreur_carnet_binance.py --verifier results\carnet_binance
 reconstruit  BTCUSDT\2026-08-16.depth.ndjson  lignes=12249  sessions=2(verifiees 1)  diffs=9011  trous=0
 reconstruit  ETHUSDT\2026-08-16.depth.ndjson  lignes=12164  sessions=2(verifiees 1)  diffs=8952  trous=0
```

9 011 différentiels rejoués reproduisent **exactement** les vingt meilleurs
niveaux des deux côtés de l'instantané reçu de la place. Deux chemins
indépendants, même résultat.

Collectes en marche :

```
.venv\Scripts\python.exe tools\etat_services.py
services :
  live_demo    ok        pid 26016
  dashboard    ok        pid 15744
  analystes    ok        pid 10572

collecteurs de marche :
  enregistreur_quotes          ok        pid 16168
  enregistreur_carnet_binance  ok        pid 32916
```

L1 Axi archive BTCUSD, ETHUSD, XAUUSD, EURUSD, US500 ; le 16/08 étant un
dimanche, seuls les deux crypto-CFD produisent des ticks — cohérent.

## Volume, mesuré et non estimé

| | par symbole | deux symboles |
|---|---|---|
| L2 Binance, clair | 27 Mo/h | **1,4 Go/jour** |
| L2 Binance, compacté | — | 0,18 Go/jour, **5 Go/mois** |
| L1 Axi | 0,27 Mo/h | négligeable |

Taux de compaction mesuré : **12,6 %**. Disque libre : 222 Go.

PAXGUSDT a été écarté des symboles par défaut : pas d'équivalent L1 archivé, et
XAUUSD n'a pas une microstructure comparable.

## Risques résiduels

1. **Le carnet Binance ne valide pas ce qu'on exécute.** Axi est un dealer CFD
   sans carnet central : `pegged`, `iceberg`, `post_only` et `market_making`
   resteront inexécutables sur le compte où V14 trade. Cette archive mesure une
   fidélité, elle n'autorise aucune exécution.
2. **Le L3 reste hors de portée.** `@depth` est agrégé par niveau de prix : pas
   d'identifiant d'ordre, donc pas de position dans la file. La fidélité de
   `post_only`, `cancel_replace` et `iceberg` montera sans se fermer.
3. **La compaction n'est pas automatique.** Sans passage régulier, 42 Go/mois.
4. **`ARRETER_V14.bat` n'arrête pas les collectes** — volontaire : arrêter le
   bot pour une raison sans rapport ne doit pas trouer l'archive. Corollaire :
   elles survivent à un arrêt de V14 et il faut fermer leurs fenêtres à la main.
5. **`tools/gitnexus_team.ps1 sync` a dépassé 30 minutes** et a été interrompu.
   Aucun verrou laissé, aucun processus orphelin (vérifié). L'analyse d'impact
   avant modification de `etat_services.py` a donc été faite à la main par
   `git grep` : trois appelants — `DEMARRER_V14.bat`, `ARRETER_V14.bat`,
   `titanium/web/command_center.py` — dont le contrat est préservé par le
   paramètre `noms` de `racines()`, valeur par défaut inchangée.

## Suite naturelle

Rejouer les six politiques dépendantes du carnet quand quelques semaines seront
accumulées, avec une fidélité **mesurée** au lieu de postulée. Rien avant.
