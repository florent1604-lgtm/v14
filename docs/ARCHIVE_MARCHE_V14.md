# Archives de marché V14

Deux archives, deux places, un seul but : mesurer les politiques d'exécution sur
des données réelles au lieu d'un carnet inventé.

## Pourquoi

La matrice d'exécution (`docs/RAPPORT_BACKTEST_15_POLITIQUES.md`) classe quinze
politiques sur des quotes **synthétiques**. Six d'entre elles dépendent d'un
carnet d'ordres — `pegged`, `iceberg`, `post_only`, `cancel_replace`,
`market_making` et, en partie, `adaptive` — et leur fidélité plafonne entre 0,35
et 0,50 parce que le simulateur invente ce carnet.

Axi ne diffuse aucun L2 : `market_book_add` rend `False`. Binance en diffuse un
toutes les 100 ms, sans clé API, et V12 le recevait déjà sans jamais l'écrire :
`OrderBookState.history` est un anneau de 30 instantanés en RAM.

Un tick ou un carnet non enregistré ne se retrouve pas. C'est la seule donnée du
projet qui soit strictement irremplaçable : les barres OHLCV se retéléchargent,
un carnet d'il y a trois semaines n'existe nulle part.

## Les deux archives

| | L1 Axi | L2 Binance |
|---|---|---|
| outil | `tools/enregistreur_quotes.py` | `tools/enregistreur_carnet_binance.py` |
| source | MT5, `copy_ticks_range` | `data-stream.binance.vision`, flux publics |
| contenu | bid/ask/last/volume, tick par tick | instantanés + différentiels 100 ms, transactions agrégées |
| dossier | `results/quotes/<SYMBOLE>/<jour>.ndjson` | `results/carnet_binance/<SYMBOLE>/<jour>.{depth,trades}.ndjson` |
| horloge | heure serveur **corrigée** en UTC | UTC natif |
| ce que ça valide | ce que V14 peut réellement exécuter | ce que V14 ne peut aujourd'hui que simuler |
| clé/compte | aucun (lecture seule MT5) | aucun (hôte données de marché) |

Les deux sont append-only, portent `horloge: "utc"` sur chaque ligne, et
n'envoient aucun ordre. Un test AST le verrouille dans chaque module.

## Lancer

```bat
COLLECTE_V14.bat
```

Deux fenêtres s'ouvrent ; les fermer arrête la collecte. Sans MT5 ouvert, seule
l'archive Binance démarre — ce script ne lance jamais MT5, c'est une décision
humaine.

Individuellement :

```powershell
.venv\Scripts\python.exe tools\enregistreur_quotes.py --symboles BTCUSD ETHUSD XAUUSD
.venv\Scripts\python.exe tools\enregistreur_carnet_binance.py
```

État à tout moment :

```powershell
.venv\Scripts\python.exe tools\etat_services.py
```

Les collecteurs y apparaissent sous « collecteurs de marche », **séparés** des
trois services. Deux raisons, opposées et toutes deux nécessaires :

- une collecte arrêtée n'est pas une panne de V14, et la compter comme telle
  ferait échouer `DEMARRER_V14.bat` ;
- le **code de sortie** reste la propriété des trois services. Un doublon de
  collecteur est signalé bruyamment mais ne le change pas : `DEMARRER_V14.bat`
  s'arrête quand ce script rend 0, et le faire échouer pour un archiveur en
  double lancerait une **seconde boucle armée** sur le même compte — l'incident
  du 08/08/2026.

`ARRETER_V14.bat` n'arrête volontairement pas les collectes : arrêter le bot
pour une raison sans rapport ne doit pas trouer l'archive.

## Format du carnet Binance

Le brut, pas un dérivé. Quatre natures de lignes :

- `instantane` — carnet complet (1 000 niveaux). `raison` vaut `amorce`,
  `trou` ou `periodique`. Un instantané périodique toutes les 15 minutes borne
  le coût d'un rejeu et rend l'archive vérifiable.
- `diff` — différentiel tel que Binance l'émet, avec `premier_id`/`dernier_id`.
- `trou` — rupture de séquence, avec le numéro attendu et le numéro reçu.
- `trade` — transaction agrégée, avec `cote_agresseur`.

Trois décisions de format méritent d'être connues avant d'écrire un
consommateur :

1. **Prix et quantités sont des chaînes**, exactement comme la place les émet.
   Un `float` à l'écriture figerait un arrondi qu'aucun retraitement ne défait.
2. **Tout différentiel reçu est écrit**, y compris antérieur à l'instantané. La
   séquence *annote* les ruptures, elle ne filtre pas : un collecteur qui décide
   de jeter une ligne est un collecteur qui peut jeter la mauvaise. Le lecteur
   retrouve l'état exact par les numéros.
3. **Une quantité nulle supprime le niveau.** La lire comme « niveau à zéro »
   laisserait un carnet plein de fantômes et gonflerait toute mesure de
   profondeur.

`cote_agresseur` dérive du drapeau `m` de Binance : `m` vrai signifie que
l'ACHETEUR tenait le marché, donc l'agresseur est le **vendeur**. C'est le seul
champ dérivé de l'archive, et le seul dont l'inversion serait invisible — les
volumes resteraient justes, le déséquilibre serait retourné.

Chaque ligne porte aussi `latence_ms`, écart entre l'horodatage d'émission de la
place et l'instant de réception locale. C'est la seule mesure de latence réelle
dont V14 dispose et elle ne se reconstitue pas après coup.

## Vérifier

```powershell
.venv\Scripts\python.exe tools\enregistreur_carnet_binance.py --verifier results\carnet_binance
```

Le contrôle rejoue chaque **session** depuis son instantané d'ouverture jusqu'au
numéro du premier instantané périodique, et compare le sommet des deux carnets.
Deux états produits par des chemins indépendants — l'un rejoué, l'autre reçu de
la place — doivent coïncider exactement.

Trois verdicts : `reconstruit`, `divergent`, `indecidable` (aucune session ne
contient encore de point de reprise, donc archive de moins d'un quart d'heure).

### La notion de session, et pourquoi elle existe

Une session s'ouvre à tout instantané `amorce` ou `trou`, c'est-à-dire à chaque
démarrage du collecteur et à chaque rupture de séquence. **Un rejeu ne franchit
jamais cette borne.**

Ce n'est pas une précaution théorique. Le 16/08/2026, le collecteur a été
redémarré pendant la mise au point : 273 mises à jour manquaient entre la fin
d'une session et le début de la suivante, et la première version du contrôle
enjambait le trou sans rien dire. Elle rendait un carnet plausible et faux — ce
qui est pire qu'une erreur, puisque rien ne le distingue d'un carnet juste.
C'est le contrôle lui-même qui l'a révélé, en refusant de valider BTCUSDT.

Un consommateur doit donc traiter `amorce` et `trou` comme des frontières : à
chacune, repartir de l'instantané et ne rien chaîner par-dessus.

Mesure du 16/08/2026, sur 60 s de collecte réelle : 264 différentiels rejoués
sur BTCUSDT et 262 sur ETHUSDT reproduisent exactement les vingt meilleurs
niveaux des deux côtés.

## Volume

Mesuré le 16/08/2026 : **0,91 Mo pour 60 s et deux symboles**, soit environ
27 Mo/h/symbole — **1,4 Go par jour** pour BTCUSDT et ETHUSDT.

La compaction ramène un jour clos à **12,6 %** de sa taille, soit environ 5 Go
par mois pour les deux symboles :

```powershell
.venv\Scripts\python.exe tools\enregistreur_carnet_binance.py --compacter results\carnet_binance
```

Le fichier du jour en cours n'est jamais touché, et le fichier clair n'est
supprimé qu'après relecture intégrale du `.gz`.

L'archive L1 Axi coûte 0,27 Mo/h/symbole : un ordre de grandeur cent fois plus
faible, la question du volume ne s'y pose pas avant plusieurs mois.

Les deux dossiers sont sous `results/`, donc hors dépôt Git.

## Le piège à ne pas oublier

Titanium exécute via MT5/Axi, un **dealer CFD sans carnet central**. Valider
`pegged`, `iceberg`, `post_only` ou `market_making` sur le carnet Binance
validerait des politiques **inexécutables sur le compte où l'on trade**.

Cette archive sert à mesurer une fidélité, pas à autoriser une exécution.
Exécuter sur Binance serait un changement de place de marché — nouveau compte,
nouvel exécuteur, mur démo↔réel à reconstruire — et c'est une décision de
Florent, pas un réglage.

Réserve de méthode : la microstructure de `BTCUSDT` sur Binance n'est pas celle
du CFD `BTCUSD` chez Axi. Même validées sur un vrai carnet, ces politiques
resteraient mesurées sur un marché qui n'est pas celui où l'argent est engagé.
