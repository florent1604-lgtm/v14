# Mise en production du lot execution — 24/08/2026

Demande de Florent (24/08, 20:26 locale) : « le test AB de Codex est termine et ne semble
pas concluant. passe en production l'integralite des codes non commites. je veux constater
par moi-meme les problemes sur un T5 Compte, DEMO en live. »

La session precedente a ete coupee au milieu du redemarrage de la boucle. Ce rapport
consolide ce qui a ete fait et l'etat verifie a 21:25 locale.

## 1. Code passe en production

| point | etat verifie |
|---|---|
| arbre de travail | propre — `git status` ne montrait aucun fichier modifie |
| HEAD | `846fe96` *docs: tracer le prochain lot execution* |
| lot integre | `d3d5a71`, `3c7c9b6`, `9c3ecf3`, `f358047`, `846fe96` |
| publication | `git push origin master:main` → `17865c7..846fe96`, **39 commits** pousses |
| synchronisation | `master...origin/main` sans ecart |

Il n'existait donc **aucun code non commite** : la totalite du lot execution etait deja
scellee localement mais jamais publiee. La mise en production a consiste a (a) publier,
(b) reparer l'environnement, (c) redemarrer la boucle sur ce code.

## 2. Environnement du venv repare

`pytest` ne collectait plus : 14 erreurs `ModuleNotFoundError`. Quatre dependances
declarees dans `pyproject.toml` manquaient du venv :

`python-dotenv`, `langchain-anthropic`, `langchain-google-genai`, `langchain-openai`

Installees dans `.venv` (48 s). Aucun changement de code, aucune modification de
`pyproject.toml`.

## 3. Preuves de non-regression

```
.venv\Scripts\python.exe -m pytest -q --no-header -p no:cacheprovider
2295 passed, 2 skipped, 18 warnings, 69 subtests passed in 200.90s
```

Code retour 0, execute a 21:21 locale sur le code exact qui tourne en production.
Les deux `skipped` sont attendus (`langchain_aws` absent, `DEEPSEEK_API_KEY` non defini).
Le fichier `.pytest_cache/lastfailed` contenait encore les 17 entrees de la collecte
cassee d'avant l'installation des dependances : il est perime, la suite complete est verte.

## 4. Boucle de trading redemarree sur le nouveau code

- Arret propre de l'arbre `live_demo` (pids 3508 + 28300), zero survivant verifie.
- Relance par `call "C:\Users\flore\Desktop\V14\RELANCER_BOUCLE.bat"` a 20:36:21.
  Les deux premieres tentatives ont echoue : `cmd /c RELANCER_BOUCLE.bat` et l'appel nu
  ne resolvent pas le `.bat` depuis le kernel — il faut `call` avec le chemin absolu.
- Etat des services a 21:17 : **une seule instance de chaque**, plus aucun doublon.

```
live_demo    ok        pid 31624   (demarree 20:36:22)
dashboard    ok        pid 26368
analystes    ok        pid 10632
```

Les fenetres `cmd /k` orphelines (26224, 11692, 24512, 27608, 23068, 21308, 5208) n'ont
plus d'enfant Python : ce sont des terminaux morts, sans effet.

## 5. Production observable — 45 minutes de vie

`results/loop_heartbeat.json`, horodate 19:18:19 UTC :

| indicateur | valeur |
|---|---|
| arme | oui |
| tours depuis la relance | 36 |
| actifs evalues | 1 780 |
| verdicts ENTER | 359 |
| ordres envoyes | 4 |
| limites placees / executees | 4 / 1 |
| univers portable | 50 actifs |
| couverture du journal | 256/256 clotures MT5, taux de manquant 0,0 % |

Compte DEMO 10055401 : solde 3 360,14 EUR, equite **3 516,97 EUR**, 14 positions ouvertes,
flottant **+156,83 EUR**. Une position neuve (AUS200) a ete ouverte depuis la relance :
la chaine decision → ordre → suivi fonctionne de bout en bout sur le nouveau code.

Refus les plus frequents apres ENTER, visibles en direct dans `results/refus_live.ndjson` :
`MAX_PAR_SYMBOLE` (190), `FX_SUSPENDU` (51), `LIMIT_PENDING_CAP` (16), `COUT_SPREAD` (15).

## 6. Ce que Florent peut constater lui-meme

- Tableau de bord : service `dashboard` actif (pid 26368).
- Etat compte a la demande : `.venv\Scripts\python.exe tools\etat_compte.py`
- Etat services : `.venv\Scripts\python.exe tools\etat_services.py`
- Flux vivants : `results/refus_live.ndjson`, `results/limit_lifecycle.ndjson`,
  `results/loop_heartbeat.json`, `results/shadow_prod.ndjson`.

## 7. Risques residuels

1. **Le test A/B execution reste non concluant.** Le cycle limite mesure sur l'historique
   affiche `net_pnl_r` = **-19,72 R** pour 688 limites placees, 54 % de remplissage et une
   economie moyenne de +0,084 R. L'economie par ordre rempli ne compense pas l'attrition
   des 315 ordres expires. C'est exactement ce que Florent veut voir en direct ; aucun
   parametre n'a ete change pour le masquer.
2. **FX toujours suspendu** (`FX_SUSPENDU` 51 refus) et **anti-fade leve** : ce sont les
   deux decisions metier en backlog (`95cb60be`, `d0bdf463`). Elles restent ouvertes.
3. Le venv avait derive de `pyproject.toml` sans que rien ne le signale ; un controle de
   dependances au demarrage manque encore.

## 8. Fichiers touches

Aucune modification de code. Deux fichiers temporaires de la session precedente supprimes :
`_probe_deps.py`, `_stop_probe.ps1`. Arbre propre, synchronise avec `origin/main`.

PAPER/DEMO only. Aucun ordre reel, aucun `.env` lu, aucune position modifiee a la main.
