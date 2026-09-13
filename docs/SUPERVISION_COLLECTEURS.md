# Supervision des collecteurs V14

## Perimetre

La supervision est distincte des moteurs : elle ne connait aucun compte MT5,
ne charge pas `.env`, ne lance aucun ordre et ne modifie aucun seuil.
Seul `collecteur_microstructure.py` (endpoints publics BTC/ETH) peut etre relance.
Quotes MT5 et carnet L2 sont recenses, **sans reprise automatique ni validation
de leurs archives**. `OK_PUBLIC_ONLY` ne signifie donc pas « V14 operationnel ».

## Utilisation Windows

```powershell
# Une passe de diagnostic, sans demarrage
.venv/Scripts/python.exe -X utf8 tools/superviser_collecteurs.py
# Une passe avec reprise publique bornee
.venv/Scripts/python.exe -X utf8 tools/superviser_collecteurs.py --apply-public
# Installer la surveillance automatique (session utilisateur ouverte)
powershell -NoProfile -File tools/installer_supervision_collecteurs.ps1
Start-ScheduledTask -TaskName V14-Collecteurs-Publics
Get-ScheduledTaskInfo -TaskName V14-Collecteurs-Publics
```

La tache utilise `pythonw.exe`, sans fenetre ni privilege administrateur.
Le superviseur controle environ chaque minute; le planificateur le relance
s'il quitte. Deux verrous OS (daemon et passe) et `IgnoreNew` empechent les
supervisions concurrentes; le collecteur dispose aussi de son verrou d'ecriture.
L'installation conserve une tache compatible; une homonyme differente est refusee.
La surveillance ne fonctionne pas pendant la veille ou hors session utilisateur.

## Diagnostic et preuves

Dans `results/supervision_collecteurs/` :

- `health.json` : dernier controle atomique, PID racines, fraicheur par actif,
  quorum de places, espace disque et decision de reprise.
- `events.ndjson` : historique append-only des controles.
- `restarts.json` : reservations de reprise durables, avant lancement du processus.
- `public_*.log` : sorties des seules reprises effectuees par ce superviseur.

Toujours verifier `observed_ms` : un fichier ancien n'est pas une preuve de sante.
Le diagnostic recalcule la fraicheur depuis les donnees de chaque actif; il ne
reprend pas le statut d'un ancien heartbeat. Codes CLI : 0 = public sain,
1 = attente/degradation, 2 = verrou/stockage indisponible.

## Protections et limites

Maximum trois tentatives par heure, delais de 60 puis 120 secondes entre essais,
reserve disque minimale de 1 Gio. Inventaire inaccessible, etat corrompu ou
horloge reculee : aucune reprise. Un processus vivant mais bloque/perime est
signale, jamais tue automatiquement. Les doublons sont signales, pas supprimes.
Les journaux restent locaux : aucune notification externe n'est configuree.
La comparaison economique des strategies de sortie reste un chantier distinct.

Pour desactiver : `Disable-ScheduledTask -TaskName V14-Collecteurs-Publics`, puis
`Stop-ScheduledTask -TaskName V14-Collecteurs-Publics`. L'arret de la tache peut
arreter ses processus enfants; cela n'affecte aucun moteur de trading lance
independamment. Aucun nettoyage automatique des archives n'est effectue.

## Verification du lot (7 septembre 2026)

19 tests cibles; suite complete : **2623 passes, 2 ignores, 71 sous-tests**, en
148,06 s. Lint commun et lint strict des deux nouveaux modules Python verts.
Preuve locale : `results/supervision_collecteurs_20260907_tests.log`.
GitNexus : quatre fichiers, 40 symboles, aucun parcours existant affecte,
risque LOW apres reconstruction de l'index FTS.
Tache installee et observee `Running`; son code 267009 signifie « en cours »,
pas une reussite de collecte. Le controle reel a signale ETH perime (`WAIT`)
malgre le processus public actif : aucun doublon ni redemarrage intempestif.
