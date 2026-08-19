# Accès MT5 élargi pour la collecte de données — étude de faisabilité

**Date** : 19/08/2026 · **Auteur** : Prime Agent · **Nature** : mesure en lecture seule, aucun ordre, aucune écriture d'archive, aucun service touché.

## Question

« Tu m'as demandé un accès complet à MT5 pour étudier les données en temps réel et
accumuler plus vite des données. Est-ce toujours faisable ? »

## Réponse

Oui — et la demande était mal calibrée dans les deux sens : l'accès en lecture existe
déjà et n'a jamais manqué ; en revanche le gisement est bien plus grand que ce que
j'avais supposé le 18/08.

## État constaté au moment de la mesure

| Élément | État |
|---|---|
| `terminal64.exe` | ouvert, PID 424 |
| `live_demo` | ok, PID 19624, armé, heartbeat frais (04:59:50Z) |
| `enregistreur_quotes` | ok, PID 16168 — 5 symboles |
| `enregistreur_carnet_binance` | ok, PID 32916 |
| disque C: libre | 212 Go |

L'incident de fin de session précédente (redémarrage `live_demo` non confirmé) est clos :
la boucle tourne, 5 services sains.

## Ce que la lecture MT5 permet déjà, sans nouvelle permission

`tools/enregistreur_quotes.py` prend **tout le catalogue visible par défaut** —
`univers_portable()` — et n'est limité à 5 symboles que parce que `COLLECTE_V14.bat`
les code en dur. Aucune permission nouvelle n'est nécessaire : lecture seule, aucun
compte, aucun ordre, verrouillé par un test AST.

## Mesures

Sondes : `sonde_cout_l1.py`, `sonde_catalogue.py`, `sonde_barres3.py`,
`sonde_profondeur.py`, `sonde_retention_ticks.py`, `sonde_backfill_jour.py`.

### 1. Catalogue et coût d'une passe complète

- Catalogue courtier : **149 symboles**, tous visibles (archive actuelle : 5 ; tradés : 51 ; vus par la boucle : 103).
- Passe complète 149 symboles : **24,7 s à froid** (166 ms/symbole, premier contact),
  **0,1 s à chaud** (1 ms/symbole). Deuxième passe mesurée immédiatement après : 0,1 s.
- Conséquence : la contention avec la boucle armée à 60 s est **négligeable en régime**.
  Le coût réel est un amorçage unique d'environ 25 s.
- Nuance technique : `mt5_lock` (`titanium/data/mt5_vendor.py`) est un `threading.RLock`,
  donc **intra-processus**. Collecteur et boucle ne partagent aucun verrou ; la
  sérialisation se fait dans le terminal, au niveau IPC. Le commentaire de
  `enregistreur_quotes.py` sur « le verrou pris une fois par symbole » est inexact
  entre processus — la mesure ci-dessus le tranche.

### 2. Débit et volume en temps réel

- Catalogue entier : **8 320 à 8 551 ticks/min** → ~12 M ticks/jour.
- Ligne ndjson mesurée sur l'archive réelle : **185 à 202 octets** (moyenne 195).
- Volume brut : **2,34 à 2,40 Go/jour**, soit ~70 Go/mois. Disque libre : 212 Go.
- 19 symboles muets sur 300 s (exotiques et CFD `.fs`) : les inclure ne coûte rien.
- Sans compaction, l'archive brute tient ~2,5 mois. Avec compaction gzip quotidienne
  des jours clos (le collecteur Binance documente ~10×), on descend vers ~0,25 Go/jour.

### 3. Le gisement rétroactif — le point décisif

Le 18/08 j'ai écrit « aucune archive de barres, et un tick non enregistré ne se
retrouve pas ». La première moitié est vraie, **la seconde est fausse pour Axi**.

Barres, `copy_rates_from_pos` (plafond dur observé : 99 000 barres par appel ;
au-delà `Terminal: Invalid params`) :

| Symbole | M1 | M15 | H4 | D1 |
|---|---|---|---|---|
| EURUSD | 99 000 dep. 2026-05-14 | 99 000 dep. **2022-08-26** | 50 198 dep. 1971 | 14 344 dep. 1971 |
| XAUUSD | 99 000 dep. 2026-05-11 | 99 000 dep. **2022-06-09** | 14 948 dep. 2007 | 4 989 dep. 2007 |
| BTCUSD | 99 000 dep. 2026-06-11 | 99 000 dep. **2023-10-21** | 20 085 dep. 2014 | 4 435 dep. 2014 |
| US500 | 99 000 dep. 2026-05-08 | 99 000 dep. **2022-05-25** | 14 222 dep. 2011 | 4 286 dep. 2011 |

Temps de rapatriement : **0,5 à 0,9 s par symbole et par timeframe**. Poids : 5,94 Mo
en RAM pour 99 000 barres. `copy_rates_range` confirme la borne : 2022 rend 9 640 barres
pour un an, 2020 et avant rendent 1 barre synthétique. La profondeur M15 est donc **~4 ans**.

Ticks, `copy_ticks_range` sur EURUSD, fenêtre de 10 min :

| Recul | Ticks rendus | Latence |
|---|---|---|
| J-2 | 241 | 287 ms |
| J-7 | 132 | 208 ms |
| J-30 | 243 | 345 ms |
| J-90 | 287 | 1 025 ms |
| J-365 | 355 | 4 640 ms |

Journée complète à J-30 : EURUSD 55 422 ticks en 0,0 s ; XAUUSD 338 866 ticks en 2,4 s ;
US500 162 735 ticks en 1,2 s.

**Le courtier sert au moins 12 mois de ticks L1.** L'urgence « chaque jour sans
collecteur est perdu » ne tient donc pas pour Axi. Elle reste entièrement vraie pour le
carnet L2 Binance, qui n'est diffusé qu'en direct.

## Ce qui n'est pas faisable, ou pas par moi

- **Ouvrir ou redémarrer MT5** reste une décision humaine. Toute cette collecte suppose
  le terminal ouvert ; il l'est aujourd'hui.
- **L2 Axi : inexistant.** `market_book_add` rend `False`. Le carnet reste Binance.
- **Backfill L1 sur tout le catalogue et un an : trop lourd en brut.** 12 M ticks/jour ×
  365 ≈ 4,4 G ticks ≈ 850 Go ndjson. À faire en gzip ou parquet, et sur un
  sous-ensemble priorisé.
- Aucun besoin de `.env`, de compte, ni d'un quelconque chemin d'ordre.

## Plan proposé, par rapport coût/valeur décroissant

1. **Archive de barres, 149 symboles, M15 + H4 + D1.** ~5 min de terminal, <1 Go.
   Rend le walk-forward reproductible (`results/walk_forward/EURUSD.json` ne l'est pas
   aujourd'hui) et donne 4 ans d'historique immédiatement au lieu d'un jour par jour.
   Outil à écrire ; le socle `mt5_vendor.get_rates` existe.
2. **Élargir le collecteur L1 aux 149 symboles** + compaction gzip des jours clos.
   Coût en régime mesuré : 0,1 s par passe. Modification : `COLLECTE_V14.bat` et une
   relance du collecteur (append-only, reprise au dernier tick, aucun trou créé).
3. **Backfill L1 rétroactif priorisé** : les 51 symboles réellement tradés, 90 jours,
   stockage compressé. À chiffrer précisément avant lancement.

L'étape 1 est celle qui change le plus l'analyse des entrées, et c'est la moins chère.

## Décisions attendues de Florent

- Feu vert sur l'enveloppe disque (étape 2 : ~70 Go/mois brut, ~7 Go/mois compacté).
- Feu vert sur l'étape 3 et son périmètre, qui est la seule à consommer beaucoup.
- Maintenir MT5 ouvert.

Rien d'autre n'est bloqué : les étapes 1 et 2 relèvent de mon périmètre de développement.
