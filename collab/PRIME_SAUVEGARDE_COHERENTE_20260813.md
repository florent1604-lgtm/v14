# Sauvegarde cohérente — clôture `3c756faf` / `cb312b43`

Portée : `titanium/sauvegarde.py`, son CLI et ses tests. Aucun ordre, service,
configuration ou journal de tâches n'est modifié.

## Contrat livré

- verrou interprocessus fondé sur `O_CREAT | O_EXCL`, exercé depuis un second
  processus Python ;
- nom d'instantané ordonnable (UTC à la microseconde) complété par un UUID :
  deux appels avec le **même** `datetime` ne se collisionnent pas ;
- fichiers copiés sous `.staging/`, puis manifeste écrit en dernier et
  publication atomique vers le dossier d'instantané ; un staging interrompu
  n'est jamais compté par la rotation ;
- manifeste durablement auditable avec métadonnées source avant/après,
  sources modifiées pendant la copie et contrôle des liens explicites
  trades/positions/pending/lifecycle ;
- garantie explicitement `crash-consistant`, jamais présentée comme snapshot
  transactionnel : une source mouvante ou un lien métier incompatible donne
  `coherent: false` sans créer ni corriger de donnée.

## Preuves fraîches

- RED observé avant correctif : quatre régressions de `bb71fa4` (collision
  datetime fixe, métadonnées absentes, incohérence métier non signalée,
  staging absent) ;
- GREEN ciblé : `18 passed in 2.21s` ;
- ruff ciblé : `All checks passed!` ;
- incidents état déjà intégrés séparément par `84703f6`, vérifiés :
  `20 passed in 1.15s` ;
- suite complète : `1765 passed, 2 skipped, 69 subtests passed`.

## Limite assumée

Le verrou périmé est une reprise de secours après crash ; il ne transforme pas
la collecte en transaction ni ne fige les écrivains du dossier `results/`.
La qualification du manifeste est donc ce qui autorise ou non une lecture
conjointe des artefacts.
