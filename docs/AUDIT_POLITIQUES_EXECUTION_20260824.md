# Les politiques d'execution jugees sur des barres M5 reelles — version 3

24/08/2026. **Cette version remplace les versions 1 et 2.** La revue finale
Codex/Hermes a trouve trois biais residuels dans la version 2 : quatre
politiques dynamiques n'etaient pas executees fidelement par le runner M5, la
mesure appelait « touche » un service reconstruit par le simulateur, et
`v14_live` etait comparee au marche sur des cohortes differentes. La version 3
separe donc contact inclusif, franchissement strict et service synthetique,
exclut les politiques non fideles, et compare `v14_live` au marche uniquement
sur les memes `decision_id`. **Aucun classement n'autorise une promotion.**

## 0. Ce que la version 1 affirmait, et qui ne tient pas

> « L'execution passive rend +0.1958 R contre +0.0658 R en taker. `v14_live`
> est deuxieme sur seize. `adaptive` est le seul candidat serieux. »

Ces trois phrases reposaient sur quatre erreurs de mesure. Deux d'entre elles
fabriquaient l'avantage passif ; une troisieme comparait la politique du bot a
une copie de la politique du bot ; la quatrieme classait des politiques que la
donnee ne separe pas. Elles sont corrigees ci-dessous, chacune avec sa mesure.

## 1. Les six corrections, une par une

### P0-1 — L'OHLC de MetaTrader5 est le BID, pas un milieu

La version 1 posait `bid = close - spread/2`, `ask = close + spread/2`. Le
courtier, lui, cote l'OHLC **au bid** : `bid = close`, `ask = close + spread`.
Le bug ne deplace pas seulement les prix, il **casse la symetrie achat/vente** :
la limite d'achat descendait d'un demi-spread (plus difficile a toucher) et la
limite de vente descendait aussi (plus facile a toucher).

Mesure sur 12 symboles liquides, 150 decisions chacun, politique `v14_live` :

| convention | touche ACHAT | touche VENTE | ecart |
|---|---|---|---|
| fautive (close = milieu) | 82.11 % | 99.07 % | **16.96 pts** |
| corrigee (close = bid) | 90.73 % | 92.80 % | 2.07 pts |

Un banc qui repond « les ventes passent mieux que les achats » de 17 points
alors que rien dans le marche ne le justifie ne mesure pas le marche.

### P0-2 — Une vente se touche sur le haut de l'ASK

`titanium/execution_sim/matching.py` compare la limite de vente a
`market.high`. Comme l'archive est cotee au bid, il faut lui passer
`high_ask = high_bid + spread`. Un snapshot porte donc desormais **`low` = plus
bas du BID** (ce qu'un achat peut toucher) et **`high` = plus haut de l'ASK**
(ce qu'une vente peut toucher). Un test execute reellement le matcher sur une
limite de vente placee entre les deux et exige qu'elle soit servie.

### P0-3 — La politique du bot n'est plus une copie du bot

La version 1 reecrivait la formule de `plan_limit_entry` dans le simulateur.
En quelques heures, la copie divergeait deja sur trois points : elle acceptait
un stop nul (la boucle live le refuse), elle n'arrondissait pas au tick (prix
99.98 sur un tick de 0.25), elle ne verifiait ni la finitude des prix ni
`ask >= bid`. Le test tolerait `1e-5`, ce qui laisse passer un prix impossible
sur un tick de 0.25.

Le prix vit maintenant dans un module unique et sans dependance courtier,
`titanium/execution/limit_pricing.py`. La boucle live et le simulateur
**appellent la meme fonction** ; un test le prouve par identite d'objet
(`limit_orders.plan_limite_entree is policies.plan_limite_entree`), et dix cas
de parite exigent l'**egalite exacte** des prix et des durees de validite, dans
les deux sens et sur des ticks non decimaux. Dix-huit cas d'entree douteuse
exigent un **echec ferme** : aucun ordre, jamais un ordre de repli.

### P0-4 — Plus aucun regard vers le futur

C'est la correction qui change le plus les chiffres. La version 1 horodatait
une barre a son **ouverture** tout en livrant immediatement son close, son haut
et son bas. Une decision prise a `t` etait donc planifiee ET appariee sur la
barre `[t, t+300)` : le prix de l'ordre venait d'un close encore inconnu, et la
touche venait d'un parcours encore inconnu.

Regle appliquee : **on planifie sur la derniere barre CLOSE, on apparie sur les
barres suivantes.** Le snapshot de decision a une enveloppe volontairement
degeneree (`low = bid`, `high = ask`) : a l'instant precis de la decision, seul
le sommet du carnet existe.

Mesure du regard vers le futur, memes 12 symboles, memes 1 800 decisions :

| construction | touche `post_only` | touche `limit_passive` |
|---|---|---|
| regard vers le futur (v1) | 98.94 % | 99.94 % |
| causale (v2) | **58.17 %** | 98.67 % |

Quarante points de taux de touche de `post_only` etaient **entierement
fabriques** par la connaissance anticipee du close.

Corollaire, releve par Hermes : un ordre valable 120 s sur des barres de 300 s
n'est pas mesurable. La barre qui contient son expiration ne dit pas QUAND elle
a touche, et le simulateur pourrait declarer un remplissage survenu jusqu'a
180 s apres l'expiration. Ces lignes sont donc marquees **INDETERMINE**, ex
ante, a partir du seul plan de l'ordre — jamais du resultat, sinon on ne
retirerait que les touches et le taux serait biaise vers le bas.

### P0-5 — Le classement ne retient que le directionnel et le resolvable

Sorties du classement, mesurees mais publiees a part :

* `market_making`, `multi_leg_simultaneous`, `maker_then_hedge_taker` : elles
  ne repondent pas a la question « comment entrer dans une position
  directionnelle deja decidee ». Le maker-puis-hedge etait de plus compare
  **sans le cout de sa couverture**, que `_prix_moyen` ecartait.
* `adaptive`, `twap`, `iceberg` : toutes leurs tranches tombent dans la meme
  barre M5. Le banc peut les executer, il ne peut pas les **distinguer**. Leur
  donner un rang, c'est presenter un ordre du bruit comme un resultat. La
  detection est faite sur les decalages programmes reels, pas sur une liste
  ecrite a la main.

`_prix_moyen` filtre desormais sur le sens de l'intention : une jambe opposee
remplie ne peut plus ameliorer le prix d'entree d'un achat.

### P0-6 — Aucun artefact n'est lu sans etre valide

La version 1 ouvrait `trades.ndjson` directement. La version 2 exige, pour
chaque symbole : `artifact_type = v14.offline_replay.trades`,
`schema_version = 2`, **l'empreinte moteur courante** (`051f50ad…`), puis le
validateur du rejeu lui-meme (`artefact_brut_valide` : sceau du manifeste,
octets et sha256 du fichier de trades, sceau du resume, compteurs, identifiants
uniques, arithmetique `gross - cost = net`). Sans cela, aucune decision n'est
rendue.

Note de version : **il n'existe pas de `schema_version = 4`.** « v4 » designe
la generation du backfill ; le contrat machine reste 2 (rectification Codex,
hub offset 588). Un test exige que `schema_version = 4` soit **refuse**.

## 2. Le classement corrigé (contrat v3)

Univers complet : **147 symboles** valides (USDCOP et USDUSC refuses,
manifeste absent — ils sont hors univers), 200 decisions echantillonnees par
symbole, **29 400 decisions**, 382 200 lignes (decision x politique).
Fenetre 12 barres = 1 h.

| politique | effet R jugement | service synthétique | contact inclusif | statut |
|---|---:|---:|---:|---|
| post_only | +0.409166 | 72.04 % | 99.07 % | classable M5 |
| limit_passive | +0.411136 | 94.51 % | 99.07 % | classable M5 |
| fok | -0.741279 | 99.92 % | 100.00 % | classable M5 |
| ioc | -0.741637 | 100.00 % | 100.00 % | classable M5 |
| market | -0.742100 | 100.00 % | non applicable | référence |

L'ordre du tableau suit le score de classement, mais `post_only` et
`limit_passive` ne sont que des **scénarios synthétiques** : le contact OHLC
proche de 99 % ne prouve ni la priorité de file, ni la quantité réellement
servie.

Hors classement : `adaptive`, `iceberg` et `twap` (séquence intra-barre non
résolue) ; `cancel_replace`, `pegged`, `pov` et `vwap` (runner M5 non fidèle à
leur logique dynamique) ; `v14_live` (17 150 décisions indéterminées et cohorte
résolue incomplète).

## 3. Comment lire ce tableau — et pourquoi il ne promeut rien

**Aucun chiffre de ce tableau n'est un taux de remplissage.** Le JSON v3
publie trois faits distincts : `taux_contact_inclusif` (le prix appartient à
l'enveloppe OHLC), `taux_franchissement` (le prix est strictement dépassé) et
`taux_service_synthetique_scenario` (le matcher reconstruit décide un service).
Seul le premier est directement observable sur M5 ; aucun ne prouve un fill
passif. Cette séparation empêche désormais de présenter le modèle de file
comme une observation du marché.

Trois lectures s'imposent avant toute conclusion.

**a) L'esperance de base est franchement negative.** `market` rend
**-0.7421 R par decision** au segment de jugement. Le probleme de V14 n'est pas
son execution : c'est que la porte d'entree, telle qu'elle est, perd. Optimiser
l'entree d'un flux perdant deplace la perte, elle ne la supprime pas.

**b) Le « gain d'entree » de +0.96 R des politiques passives n'est pas un
resultat, c'est la taille du spread.** Il dit que, sur l'univers complet et sur
les decisions retenues, le spread pese en moyenne pres d'un R entier. Ce sont
les instruments exotiques a spread large qui dominent cette moyenne. Croire
qu'une limite passive capture ce spread gratuitement suppose exactement ce que
le banc ne peut pas voir : la file d'attente. Une limite au bid qui est
seulement **effleuree** n'est presque jamais servie.

**c) A `effet_r` par decision, la politique qui trade le moins gagne
mecaniquement.** Quand l'esperance de base est negative, ne pas etre servi
vaut zero, donc mieux que la moyenne. `post_only` est premiere avec 72 % de
service synthétique ; ce n'est pas une performance, c'est une abstention. Le
rapport publie donc toujours ensemble effet, service synthétique, contact et
franchissement.

## 4. Ce que la correction fait a la politique du bot

`v14_live` est **la seule politique dont la mesure soit majoritairement
impossible** : 17 150 lignes indeterminees sur 29 400, soit **58 % des
decisions**. La raison est structurelle : sa duree de validite tombe a 120 s
des que le spread depasse 15 % du R, et 120 s ne tient pas sur une grille de
300 s. Ces 58 % sont precisement les decisions a spread large — celles ou le
gain passif serait le plus gros.

Autrement dit : **sur la moitie du flux, ce banc ne peut rien dire de la
politique qui tourne reellement.** Sur la cohorte de jugement résolue commune
à `v14_live` et `market` (`n = 4 331`), le scénario synthétique donne
`v14_live = +0.186829 R`, `market = +0.066861 R`, soit un uplift de
`+0.119968 R`. Le bootstrap par grappes d'actifs (5 000 tirages, seed 14)
donne un IC95 de `[+0.106311 ; +0.133653] R` quand chaque décision conserve
son poids, et `[+0.106900 ; +0.143620] R` quand chaque symbole a le même
poids. Le signal est donc stable sur la cohorte résolue, mais il ne s'étend
pas automatiquement aux 5 518 décisions de jugement indéterminées : le taux
de résolution n'est que de 43,974 % et l'attrition est probablement liée au
spread et au TTL. Cela reste une borne modélisée, sans preuve de file L1.

Consequence directe et actionnable : soit la boucle aligne ses TTL sur une
grille mesurable, soit la mesure passe aux ticks L1. Rien d'autre ne leve cette
indetermination.

## 5. Limites assumees

1. **Le service reste synthétique.** Contact, franchissement et service sont
   séparés, mais aucun n'est un taux de remplissage observé.
2. **La profondeur est reconstruite.** Seul le sommet du carnet est archive.
3. **Une barre M5 ne resout pas l'intra-barre.** TWAP, adaptive et iceberg
   sont indistinguables. Le runner événementiel rejoue désormais
   cancel/replace, pegged, VWAP et POV sans décision ni fill rétroactif ; ils
   restent néanmoins hors du classement M5, car la grille 300 s ne distingue
   pas leurs actions sous la barre et l'audit ne fournit pas encore le profil
   de volume historique exigé par VWAP.
4. **La trajectoire de sortie est tenue fixe.** Un meilleur prix d'entree
   change la probabilite de toucher le stop ; cette mesure ne le capte pas.
5. **Frais a zero par defaut.** Sur ces instruments le cout du courtier EST le
   spread, deja porte par chaque barre.
6. **Continuite exigee.** Une decision hors grille M5, sans barre precedente
   contigue, ou dont la fenetre contient un trou de seance, est ecartee : un
   trou est du temps non observe, pas un temps sans transaction.

## 6. Preuves

```
tools/politiques_execution_reel.py --limite 200             147 symboles, 29 400 decisions, 382 200 lignes
pytest ciblé (prix, politiques, audit)                       118 passed
pytest (suite complete)                                     2278 passed, 2 skipped
ruff ciblé                                                  All checks passed
git diff --check                                            propre
GitNexus detect_changes                                     22 symboles, 10 flux, risque high revu
empreinte moteur du rejeu                                   051f50adf179177e (147/147 artefacts)
```

Sorties machine (hors Git, `results/` est ignore) :
`results/politiques_execution_reel_v3.json`
(`sha256: 6bf946783092b1aa8619708320e4672be57a113662eb1005657b161d00f87255`) et
`results/politiques_execution_reel_v3_lignes.ndjson`
(`sha256: dad65cac643aa1f4c4b155da4dde5d8951ebb536f46c9d0bd690ff0f448fbcce`).
Le second contient 382 200 lignes portant
`decision_id`, `decision_at`, `side`, `prix_entree_simule` — l'appariement est
donc reauditable, ce que la version 1 rendait impossible.

## 7. Ce qui reste, et pour qui

**Aucun changement de politique d'execution live n'est appliqué.** Le lot
commitable corrige l'outillage et les preuves ; il ne promeut aucune stratégie.

Quatre tâches en découlent, dans cet ordre :

1. **La porte d'entree, pas l'execution.** Un flux a -0.74 R par decision ne
   se repare pas par un meilleur prix d'entree.
2. **Le banc L1 de contact est livré**, avec cutoff, hashes, continuité stricte,
   contact et franchissement séparés. Il laisse volontairement `service=null` :
   sans profondeur, file, transaction séquencée ni côté agresseur, aucune
   politique passive ne peut être promue. Voir `docs/AUDIT_L1_PASSIF_20260824.md`.
3. **Le runner dynamique est causal**, mais sa réintégration au classement
   attend une source plus fine que M5 et, pour VWAP, un profil historique.
4. **Les IC appariés par symbole sont livrés.** Le prochain travail statistique
   est l'analyse de sélection résolus/indéterminés et l'interaction entre
   meilleur prix d'entrée et trajectoire de sortie.
