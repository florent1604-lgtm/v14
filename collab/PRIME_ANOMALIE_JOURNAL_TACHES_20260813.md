# Anomalie du journal de tâches — ligne 68 invalide

**Constat Prime, 13/08/2026.** `tools/task_journal.py list` signale
`invalid_lines: 1` depuis plusieurs jours. Voici ce que c'est, ce que ça a coûté,
et pourquoi le journal n'est **pas** réécrit.

## La ligne

`collab/tasks.ndjson`, ligne 68 :

```text
175R, PF .597. Promotion fermee; collecte sans optimisation."}
```

C'est une **queue d'objet JSON sans tête** : une écriture déchirée. Le parseur la
rejette, ce qui est le comportement correct.

## Aucune donnée n'a été perdue

Le contenu de cette queue se retrouve **intégralement** à la ligne 72, dans un
événement complet et valide :

- ligne 72, `19c31029`, tâche `394a10ce`, acteur `codex`, à
  `2026-08-11T19:10:21.936757Z` ;
- note complète : « … 23, expectancy -.2175R, **PF .597. Promotion fermee;
  collecte sans optimisation.** »

La ligne 68 a été écrite à `19:10:11`, la ligne 72 dix secondes plus tard. La
séquence est donc : écriture déchirée, échec détecté par l'écrivain, réécriture
réussie. **Le fragment est un résidu, pas une perte.** Rien à récupérer.

## Cause

`titanium.collab_tasks._append` sérialise avec un `threading.Lock` puis fait un
seul `os.write` sur un descripteur `O_APPEND`. C'est solide **à l'intérieur d'un
processus**, et ça n'a jamais changé depuis `3b53839`.

Mais le journal a plusieurs écrivains **processus** : le CLI `task_journal.py`,
le serveur du terminal 8097, le tableau de bord. Un `threading.Lock` ne franchit
pas la frontière de processus, et sous Windows l'atomicité d'un `write` en
`O_APPEND` n'est pas garantie comme elle l'est sur POSIX. Deux écritures
rapprochées — ici `19:10:11.368` et `19:10:11.375`, à sept millisecondes — ont
pu s'entrelacer.

## Décision : on ne réécrit pas

Le journal est append-only par contrat. Supprimer la ligne 68 rendrait le
compteur propre et détruirait la trace de l'incident, qui est précisément ce qui
permet aujourd'hui de prouver qu'aucune donnée n'a été perdue. `invalid_lines: 1`
reste donc affiché, et sa signification est consignée ici.

**Ce qui doit changer, c'est le futur, pas le passé** : verrou inter-processus
(fichier de verrou ou `msvcrt.locking` sous Windows) autour de l'ajout, ou
écrivain unique derrière le service. Tant que ce n'est pas fait, un second
fragment reste possible.

## Doublon du 13/08

Deux tâches strictement identiques ont été créées à six secondes d'intervalle :
`a994727b` (10:42:19) et `cf13cb28` (10:42:25), suite Hermes M1 sur la
quarantaine `SORTIE_INTROUVABLE`.

Cause : l'appelant affichait la sortie du CLI **tronquée aux 400 derniers
caractères**, ce qui rend le JSON impossible à relire ; la création avait
pourtant réussi. Conclusion erronée à l'échec, puis relance.

- **`a994727b` est la source de vérité** (la première).
- `cf13cb28` est passée en `blocked` avec une note explicite : à ne pas
  travailler.

Là encore, aucune ligne n'est réécrite : la correction est un état et une note,
pas une suppression.
