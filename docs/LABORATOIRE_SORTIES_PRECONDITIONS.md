# Laboratoire de sorties : qualification des entrees

## Ce lot ne change pas les sorties du moteur

`tools/audit_quotes_sorties.py` qualifie les archives bid/ask avant un rejeu causal.
Il ne calcule ni nouvelle strategie ni gain attendu et n'appelle pas MT5.
Il ne remplace pas les anciens outils de contrefactuel : leurs bornes sur MFE
ne constituent pas une reproduction de la baseline.

```powershell
.venv/Scripts/python.exe -X utf8 tools/audit_quotes_sorties.py `
  --symboles BTCUSD ETHUSD --max-gap-ms 60000 `
  --json results/audit_quotes_nouveau.json
```

Le rapport doit etre nouveau et en dehors de `results/quotes` : un rapport
existant n'est jamais ecrase. Les donnees restent privees, le code seul est commite.
L'audit lit en streaming la taille initiale de chaque fichier; chaque source
porte son SHA-256 et un controle de stabilite taille/date/identite pendant lecture.
Les exemples de trous sont limites a vingt, mais tous sont comptes.

## Interpretation

- `INVALID` : fichier absent/vide/instable, ligne tronquee ou invalide, quote
  croisee/non finie, symbole/date/horloge incoherents, ou temps retrograde.
- `GAPPED` : structure valide, mais au moins un intervalle depasse le seuil
  explicite (60 secondes par defaut), y compris entre deux fichiers journaliers.
  Cela peut correspondre a une fermeture de marche : ce n'est pas automatiquement
  une corruption. Il faut croiser le calendrier et les episodes de position.
- `STRUCTURALLY_VALID` : ces controles passent, **pas** une certification que
  tous les ticks du courtier sont presents.

Aucun tri des ticks, interpolation, retrait de ligne ou correction d'horloge
silencieux. Les quotes consecutives identiques sont comptees sans etre assimilees
a une corruption; plusieurs evenements peuvent partager la meme milliseconde.
Codes CLI : 0 structure valide, 1 anomalies/trous; les erreurs d'usage echouent.

## Conditions encore necessaires avant optimisation

`ready_for_exit_optimization` reste explicitement `false` dans ce premier lot.
Il faut ensuite :

1. Croiser chaque admission/cloture avec les quotes du meme actif et de la meme
   horloge; ne jamais supprimer silencieusement les episodes non couverts.
2. Reproduire la baseline avec le vrai gestionnaire de positions et un ATR
   disponible a l'instant de decision, pas un indicateur calcule dans le futur.
3. Reconstituer commissions, swap, frais et slippage, puis admissions, reentrees
   et contraintes portefeuille identiques pour toutes les variantes.
4. Comparer les familles de sortie hors echantillon chronologique, avec mesure
   d'incertitude; ne promouvoir aucun seuil a partir de cet audit de fichiers.

## Controle BTC/ETH du 7 septembre 2026

Rapport prive : `results/audit_quotes_sorties_20260907.json`.
Vingt fichiers, du 15 au 24 aout, controles sans modification des sources :

| Actif | Quotes valides | Trous > 60 s | Plus grand trou | Verdict |
|---|---:|---:|---:|---|
| BTCUSD | 2 329 065 | 13 | 3,608 h | GAPPED |
| ETHUSD | 2 207 655 | 13 | 3,607 h | GAPPED |

Aucune anomalie structurelle detectee. Dernieres quotes : 24 aout 2026 a
10:58:32 UTC (BTC) et 10:58:36 UTC (ETH). Ces archives ne couvrent pas les
episodes posterieurs; ni le comblement des trous ni le rejeu causal complet
ne sont effectues par ce lot. La cause des trous n'est pas attribuee sans
croisement avec les seances et journaux de fonctionnement.

Validation : 22 tests cibles; suite complete **2645 passes, 2 ignores,
71 sous-tests**, en 148,27 s. Lint commun et strict des nouveaux modules verts.
Preuve : `results/audit_quotes_sorties_20260907_tests.log`.
GitNexus : six fichiers (dont deux compteurs d'index generes), aucun parcours
existant affecte, risque LOW. Aucun fichier de moteur scelle n'a ete modifie.
