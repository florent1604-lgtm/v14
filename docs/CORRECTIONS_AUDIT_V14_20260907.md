# Corrections de l'audit V14 — 7 septembre 2026

## Périmètre livré

Suite de `AUDIT_MODERNISATION_V14_20260906.md`, base `ad3d57a`.
Les corrections d'observabilité P0 étaient déjà commitées. Ce lot implémente
le socle P1 et une partie de P3 ; il ne prétend pas terminer le laboratoire P2
ni démontrer une amélioration de rentabilité.

### Exécution : intention durable avant envoi

`titanium/execution/execution_ledger.py` enveloppe l'exécuteur d'entrée de
`tools/live_demo.py`, après les portes existantes. SQLite/WAL, synchronisation
FULL et réservation transactionnelle précèdent l'appel au courtier.

- Clé stable compte/serveur/symbole/barre, indépendante des mises à jour de
  politique : doublons bloqués après redémarrage et entre processus.
- Politique/version, risque, distance du stop et cible conservés avant l'envoi.
- Corrélation explicite dans le commentaire MT5, conservant `titanium-v14-`.
- Accusés, quotes bid/ask, prix demandé, ticket d'ordre/deal, volume déclaré et
  heures locales enregistrés lorsqu'ils sont disponibles. Manquant reste nul.
- `SUBMITTING`/`UNKNOWN` bloque toute nouvelle entrée du compte. Aucun retry
  automatique d'un timeout ; une panne disque après envoi ne transforme pas un
  vrai envoi en refus fictif.
- Le code MT5 `10010` est traité comme exposition partielle, pas comme refus.
  Le risque demandé reste réservé prudemment jusqu'à réconciliation/revue.
- Collecte des deals par **ordre puis identifiant de position** : remplissages
  et sorties partiels, sorties manuelles et frais disponibles sont conservés.
  Un ticket modifié par le courtier n'écrase pas silencieusement la preuve.
- La récupération d'un ordre par son tag n'autorise pas à le renvoyer.
  Une allocation netting ambiguë impose une revue. La protection des positions
  s'exécute avant la réconciliation : son indisponibilité bloque les entrées,
  pas le mécanisme de protection existant.

Portée : nouvelles entrées passant par `live_demo`, après chargement du code.
Ce registre ne couvre pas rétroactivement les anciennes intentions, ne remplace
pas l'idempotence des sorties et ne certifie pas encore le coût total exact.
Les horodatages historiques MT5 sont étiquetés `broker_raw`, pas arbitrairement UTC.
Les lectures broker sont bornées à 20 intentions par passage, avec rotation.

Références : [codes de retour MT5](https://www.mql5.com/en/docs/constants/errorswarnings/enum_trade_return_codes)
et [lecture des deals par ordre/position](https://www.mql5.com/en/docs/python_metatrader5/mt5historydealsget_py).

### Données crypto : fraîcheur vérifiable

Le collecteur enregistrait auparavant la même heure de réception après avoir
attendu toutes les réponses ; l'agrégateur retenait ensuite la source la plus
récente. Une source rapide pouvait ainsi masquer un carnet plus ancien.

Le correctif horodate chaque réponse, conserve le plus ancien composant utilisé,
exclut les sources périmées/futures/non finies et ne compte pas deux fois une
place. Les transactions fraîches ne rafraîchissent plus un vieux carnet Bybit/OKX.
Un dernier contrôle local intervient après la délibération et avant l'envoi.
Pour BTC/ETH pris en charge : moins de deux places fraîches ou opposition
confirmée entraîne WAIT/refus. Les instruments non couverts gardent leurs portes.

Le collecteur possède un verrou système mono-écrivain et publie
`results/microstructure/_health.json` : fraîcheur par actif, nombre de places,
heure d'observation et PID. Un processus vivant n'est pas assimilé à une donnée
saine. Ce fichier n'est **pas** un ordonnanceur ni une notification externe.

## Validation et état opérationnel

Commandes depuis la racine :

```powershell
.venv/Scripts/python.exe -m pytest tests/ -q
& 'C:\Program Files\Git\bin\bash.exe' tools/lint_gate.sh
.venv/Scripts/python.exe -X utf8 tools/rapport_execution.py
.venv/Scripts/python.exe -X utf8 tools/etat_services.py
```

Validation finale : **2 604 réussis, 2 ignorés, 19 avertissements, 71 sous-tests
réussis en 153,01 s** ; lint commun et lint strict des nouveaux modules verts.
Les deux skips concernent Bedrock optionnel absent et le test API DeepSeek sans
clé. Sortie privée : `results/audit_corrections_20260907_tests.log`.
Les deux tests structurels qui visaient l'ancienne syntaxe d'appel ont été
adaptés au wrapper ; leurs assertions d'ordre des portes restent présentes.
Un test AST supplémentaire vérifie l'exécuteur, les arguments de sûreté et
l'absence d'envoi direct dans la boucle. Aucun test de sûreté n'a été supprimé.

GitNexus : analyses upstream LOW/MEDIUM ; le diff agrégé affecte neuf parcours
et est classé HIGH. La panne récurrente FTS a nécessité une reconstruction
`analyze --force --skip-skills --index-only`. Cela répare l'index courant, pas
le défaut amont de l'indexation incrémentale.

Au contrôle du 7 septembre, les trois services métier sont arrêtés et le registre
rend `NOT_STARTED`. Les changements d'exécution ne sont donc pas chargés dans
un moteur actif. Aucun réarmement, ordre de test ou modification de `.env`.
Les 11 fichiers scellés et les artefacts de rejeu sont inchangés.
Seul le collecteur public BTC/ETH a été relancé en fenêtre cachée, sans doublon.
Le champ `status` de son fichier santé décrit sa dernière observation : toujours
vérifier aussi `observed_ms`, un ancien `OK` n'est pas une preuve de fonctionnement.

## Suite nécessaire, sans prétendre à un résultat déjà acquis

1. Chargement en DEMO lors d'une reprise explicitement autorisée de l'exécution ;
   contrôler la chaîne réelle dès la première intention et tout état UNKNOWN.
2. Reprise/supervision persistante des archives quotes MT5 et carnet L2, encore
   non traitée ici. Le collecteur public ne remplace pas les quotes du courtier.
3. Laboratoire P2 : mêmes admissions, quotes bid/ask ordonnées, ATR causal,
   réentrées, frais et portefeuille ; reproduction de la baseline avant toute
   comparaison walk-forward des familles de sortie. Ne pas optimiser sur MFE
   seul ni augmenter le risque avec cet échantillon historique négatif.

Réversibilité : revert du commit de ce lot, sans supprimer le registre ni les
archives. Ne pas revenir à l'ancien exécuteur sur une intention incertaine :
réconcilier d'abord, pour ne pas perdre la protection contre les doublons.
