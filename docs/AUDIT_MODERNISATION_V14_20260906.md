# Titanium V14 — audit et modernisation, 06/09/2026

## Verdict et risques prioritaires

**La priorité n'est pas de rendre Hermès plus agressif : il faut distinguer une
opportunité, un envoi, un remplissage et un résultat attribuable à une politique.**
La perte historique est confirmée. Une nouvelle politique rentable n'est pas démontrée.
Ce lot corrige des mesures et la chaîne de développement, pas les seuils de trading.

**Risque d'intégration final : MEDIUM, quatre parcours affectés**, après
reconstruction complète de GitNexus et comparaison avec `43bf65f` (17 fichiers,
52 symboles). Les premières analyses annonçaient CRITICAL/285 parcours, même
pour un test isolé. La synchronisation a ensuite révélé l'incohérence de
`file_fts` ; l'index a été reconstruit, puis la synchronisation incrémentale
a réussi. Le contrôle sur cet index sain remplace ces premières estimations.
Diff relu, suite complète verte, imports d'analyse hors MT5 et activation métier différée.

État relevé le 06/09 à 11:10 UTC : boucle active, equity publiée 1 971,83,
6 531 ENTER bruts, zéro envoi depuis ce démarrage. Les 4 698 exclusions de coût
multi-timeframe et les 1 833 refus d'intelligence expliquent ici exactement ces
6 531 évaluations. Ce ne sont **pas** autant d'opportunités indépendantes.
`live_demo`, dashboard et analystes sont actifs ; les trois collecteurs
complémentaires sont arrêtés. Derniers motifs lus à 11:14 UTC : mémoire d'edge
insuffisante sur LTCUSD et UNI-USD. Aucun filtre n'a été contourné.

Commandes de contrôle, qui donnent l'état courant et non une photographie historique :

```powershell
.venv/Scripts/python.exe -X utf8 tools/etat_services.py
$h = Get-Content results/loop_heartbeat.json -Raw | ConvertFrom-Json
$h | Select-Object at,armed,equity
$h.stats | Select-Object tours,enter,envoyes,simules
$h.stats.tunnel | Select-Object multitimeframe,post_enter_refusal
Get-Content results/refus_live.ndjson -Tail 2
```

Pas de lecture de `.env`, d'appel MT5, d'armement, de changement SL/sizing,
de redémarrage du bot, de connexion Jarvis/StockSharp, ni de souscription VPS.
Les corrections de boucle/IRM seront chargées lors d'un prochain redémarrage autorisé.

## Preuve reproductible et limites

La synthèse versionnée est [audits/modernisation_20260906.json](audits/modernisation_20260906.json).
Elle contient les longueurs et SHA-256 des préfixes des quatre journaux ; leur
croissance ultérieure ne change pas cet échantillon. Les journaux privés ne sont
pas copiés dans Git. Pour **tous les chiffres historiques ci-dessous** :

```powershell
.venv/Scripts/python.exe -X utf8 tools/audit_modernisation.py --snapshot docs/audits/modernisation_20260906.json --output results/modernisation_verification.json
```

Choisir un autre nom si la sortie existe : l'outil refuse l'écrasement. Une ligne
finale partielle est signalée et exclue ; une ligne interne corrompue, un ticket
conflictuel ou un préfixe modifié font échouer l'audit.
La reproduction a été vérifiée par égalité des objets JSON ; l'indentation et
les représentations numériques textuelles peuvent différer, pas les valeurs.

| Mesure | Résultat vérifié | Interprétation |
|---|---:|---|
| Clôtures live, 10/08–06/09 | 780 | Historique mêlant plusieurs politiques, pas OOS |
| Espérance / PF en R / gagnants | −0,1244 R / 0,7517 / 43,46 % | PF en unités de risque, pas en euros |
| IC95 par bootstrap de jours de clôture | [−0,2257 ; −0,0131] R | 21 jours, 4 000 réplications, graine 601 ; dépendance entre jours possible |
| Coût moyen estimé / proxy brut | 0,07838 R / −0,04603 R | Décomposition exacte : 0/780 ; ce proxy ne prouve pas l'edge brut |
| Net explicitement exact | 722/780 | Les 58 autres lignes sont historiques, sans ce marqueur |
| Jointure trades–excursions | 780/780, aucun PnL divergent | Normalisation du préfixe `live:` indispensable |
| Décisions résolues retrouvées | 224/780 | Attribution versionnée encore incomplète |
| Limites placées / remplies / expirées | 690 / 375 / 315 | Participation historique 54,35 %, pas taux de réussite |
| Limites clôturées reliées au placement | 373/373 | `spread_r` vient de `placed`, pas de `closed` |
| Contextes / au moins 20 clôtures / symboles | 188 / 1 / 77 | Fragmentation réelle de la mémoire live |

### Conclusions du dossier à rectifier

1. **« TP 3 R partout » : faux.** Les distances TP initial/risque initial
   arrondies sont 2,0 R pour 754 trades, 2,1 pour 23, 2,2 pour 2 et 2,3 pour 1.
   `tour` construit actuellement `rr_ratio=2.0`. `exit_reason` journalise une
   phase de gestion, pas le motif du deal MT5 : il ne prouve pas l'absence de TP.
2. **« Trois piliers sont falsifiés » : non établi.** `support_pillars` donne
   661 trades à deux piliers (−0,1037 R) et 118 à trois (−0,2472 R), pas les
   effectifs du dossier. Après appariement classe/semaine de clôture : différence
   −0,1244 R, p exploratoire 0,247 (725 trades). Par symbole/semaine : −0,1272 R,
   p=0,294 (411 trades). Ni causalité, ni preuve d'absence d'effet : sizing inchangé.
3. **« Toute la mémoire attend 20 exemples exacts » : incomplet.** Le registre
   live est fragmenté, mais `ReplayEdgeMemory.verdict` utilise déjà un repli
   contexte → actif scellé, avec ses propres minimums. Ne pas reconstruire cette
   hiérarchie en modifiant `edge.py` sur une prémisse incorrecte.
4. **« Seul le premier filtre de coût est compté » : partiellement faux.**
   `_refus` compte déjà le troisième. Le deuxième, la coalescence multi-horizons,
   les erreurs de sizing et les candidats abandonnés au plafond des limites
   causaient des trous. L'IRM transformait ces trous en faux envois.
5. **`censored` n'est pas un indicateur de données manquantes.** Dans
   `journaliser_cloture`, il signifie `phase != trailing AND pnl_r <= 0`.
   Ses 441 valeurs vraies dépendent donc du résultat. Les retirer sélectionne
   surtout les gagnants : 300 des 339 restants atteignent 0,8 R, contre 311/780
   dans la population entière. Ni l'un ni l'autre n'est une probabilité future.

### ADX/CHOP : hypothèses, pas nouveau filtre

Quatre variables préspécifiées, permutations/Cliff/BH existants. Sur les 780
trades, aucune ne passe à la fois FDR 10 % et taille d'effet minimale.
Sur le dernier tiers chronologique, CHOP LTF apparaît (delta −0,1947,
p ajusté 0,04796). Ce tiers **n'est pas une validation hors échantillon** :
analyse descriptive répétée, corrélations temporelles et changements de politique.
La sélection des seuls « non censurés » fait apparaître ADX LTF/HTF : conservée
uniquement comme démonstration de sensibilité, jamais comme recommandation.
Les corrections BH sont intra-analyse, pas une garantie globale sur les recherches répétées.

## Axes retenus, dans mon ordre

| Priorité et état | Pourquoi / fichiers | Critère et risque / retour arrière |
|---|---|---|
| P0 — corrigé : comptabilité des ENTER | `live_demo.py`, `analysis/entry_accounting.py`, `irm.py`. Chaque horizon écarté a un motif ; l'IRM lit les vrais envois. | Tests `test_entry_accounting.py`, `test_irm.py`, `test_live_demo_telemetry.py` verts. Aucun changement de sélection. Un tour interrompu reste explicitement `INCOMPLETE`, pas artificiellement équilibré. Revert du commit télémétrie. |
| P0 — livré : audit figé et statistiques honnêtes | `analysis/modernisation.py`, `tools/audit_modernisation.py` ; correction du minimum cumulatif BH dans `discriminants.py`. | Reproduction JSON identique, sept tests d'audit et test BH. Aucune promotion automatique. Revert du commit analyse. |
| P0 — corrigé : catalogue stable | `gitnexus_team.ps1 --skip-skills`, docs du mandat Hermès. La synchronisation recréait les doublons même sans `setup`. | Catalogue canonique et miroirs identiques, 49 skills ; test du lanceur. Dossier généré déplacé dans `results/gitnexus_skills_backup_20260906`, récupérable. Revert du commit workflow si nécessaire, sans réinjecter les doublons. |
| P1 — prochain développement : attribution exécution | Intention immutable → ordre → tous les deals → position → clôtures, avec politique/version et quantité. | Couverture 100 % des nouvelles intentions, zéro orphelin silencieux ; manquant = UNKNOWN. Tests de remplissage partiel, timeout ambigu, doublon, reprise et clôture partielle. Pas encore implémenté dans ce lot. |
| P2 — laboratoire : familles de sortie | Rejeu chronologique de quotes bid/ask et ATR ; mêmes admissions, réentrées, frais et contraintes portefeuille. | Baseline reproduite, candidats préenregistrés, walk-forward et comparaison nette groupée par jour/symbole. Aucun seuil retenu sur le seul MFE. Pas de modification scellée justifiée aujourd'hui. |
| P3 — disponibilité avant puissance | Remettre les collecteurs sous supervision opérationnelle, puis comparer poste et VPS à charge identique. | Fraîcheur par source + alerte d'absence, jamais un timestamp global rafraîchi artificiellement. Redémarrage métier séparé ; dimensionnement cloud conditionné aux mesures. |

GitNexus upstream : `tour` un appelant direct/un processus ; `_organes` deux
appelants/un processus ; analyse discriminante un appelant indexé, périmètre
hors exécution. Risque retourné LOW, mais la télémétrie touche une boucle
critique et exige ses tests. PowerShell `Sync-TeamIndex` non indexé : risque
UNKNOWN, contrôlé par lecture du lanceur et test structurel. Aucun des fichiers
moteur scellés n'a été modifié : aucun rejeu invalidé par ce lot.
Le contrôle agrégé après réparation indique MEDIUM : `Main → _mt5`,
`Main → _int`, `Main → _f`, `Main → Univers_complet`, via `tour`.

## Répertoire d'Hermès et architecture choisie

**Hermès choisit la politique ; un adaptateur synchrone revalide les faits au
moment d'envoyer.** Pré-calcul avec TTL, identité de candidat, version de modèle
réellement producteur, enveloppe de risque et tactiques autorisées. Le droit
d'appeler MT5 ne signifie pas un accès sans gardes. Les clauses historiques
interdisant tout appel MT5 sont explicitement remplacées dans les documents.

Choix : **pré-calcul**, pas appel bloquant sur chaque tick ni trade spéculatif
en attendant le cerveau. On perd une partie du contexte de dernière milliseconde,
d'où revalidation du prix, spread, quote, session et politique avant envoi.
Le bloquant perd la fraîcheur ; le spéculatif engage avant validation. Les
latences du dossier n'ont pas été rebenchmarkées ici ; autorisation et promesse
de réponse instantanée sont deux choses différentes.

Le répertoire pertinent est marché / limite / taille réduite / attente bornée /
annulation. Marché, limite, modulation et refus existent déjà. La nouveauté utile
est leur **choix contextualisé mesurable**, pas cinq chemins d'envoi concurrents.
L'attente doit expirer et revalider, pas dormir dans la boucle ; l'annulation
d'un pending doit être distinguée du rejet d'une intention.

Garde-fous requis : identifiant d'intention idempotent, persistance avant envoi,
un seul écrivain MT5, délai plafond, quota d'actions, réconciliation d'un timeout
ambigu avant nouvelle tentative, aucun retry aveugle, identité compte DEMO,
interdiction d'élargir les bornes par texte libre, origine des données et TTL.
Le test `test_hermes_cortex.py` conserve le refus d'une instruction injectée dans
`requested_action`. Indisponible/périmé/incohérent ⇒ WAIT pour les entrées,
sans empêcher la gestion protectrice des positions déjà ouvertes.

Le prochain journal doit mesurer les neuf dimensions demandées : shortfall net,
remplissage, partiels, rejets, slippage favorable/défavorable, latence,
annulations et slippage des stops. Elles partagent une seule chaîne d'identités.
Segmenter symbole/sens/session/spread/volatilité/type/version ; distinguer temps
broker, UTC de décision et instant de découverte par polling. Ne pas présenter
le délai du polling comme la latence du courtier. Les 375 fills historiques seuls
ne permettent pas de calculer honnêtement ces neuf métriques.

## Pistes écartées ou reportées

- **Changer immédiatement tous les trailing/TP** : MFE/MAE et ATR d'entrée ne
  donnent ni l'ordre des excursions ni l'ATR futur, et le trade actuel a coupé
  l'observation. Chandelier/runner restent des hypothèses. Un partiel ne rend pas
  le reliquat « sans risque » : gaps, coûts et risque total subsistent.
- **Ajouter Hurst/ER ou 100 nouvelles recherches** : commencer avec les variables
  déjà mesurées et un protocole fixé. Le troisième levier oublié est l'exposition
  au niveau portefeuille : pertes corrélées, taille effective, participation,
  simultanéité et réentrées, pas seulement taux gagnant × gain moyen.
- **L2 Axi, agresseurs, file d'attente ou stop temporel** : aucune donnée neuve ne
  justifie de rouvrir les pistes déjà invalidées. Ne pas utiliser un carnet
  Binance comme s'il s'agissait du carnet CFD Axi.
- **StockSharp en production** : non retenu. Laboratoire isolé possible après
  attribution des données. Pas de seconde connexion courtier ni second moteur
  d'indicateurs divergent.

### Ce que Jarvis apporte réellement

Lecture seule de `C:\Program Files\JARVIS\titanium_connector.py`,
`agent_model_manager.py`, `frontend/package.json`, `frontend/vite.config.ts`.
Le repli StockSharp lit surtout `portfolio.csv` et du cash : ce n'est pas une
passerelle causale de quotes/exécutions. Plusieurs états antérieurs sont gardés
si une requête échoue, alors que l'instant global de rafraîchissement avance ;
`_CONTEXT_MAX_AGE` est défini sans contrôle effectif. À ne pas recopier.
Le frontal Vite/TypeScript/Three peut inspirer la présentation, pas la sûreté.
Le registre de modèles est une liste statique, pas une mesure de disponibilité.

Je conserve SQLite/WAL, chaîne de hachage et contrats datés, avec l'IRM/SSE
lecture seule existant. REST/WebSocket pourra transporter les mêmes contrats
versionnés hors exécution ; le protocole réseau seul ne garantit pas la fraîcheur.

### Limites Axi : correction documentaire importante

Le dossier assimile limites et stops. La documentation MT5 distingue leurs
conditions, et l'aide Axi indique un remplissage des limites au prix demandé
ou meilleur. Cela ne garantit **ni remplissage, ni rentabilité**. Référence
décisionnelle, prix limite et prix réalisé doivent rester trois champs distincts.
Confirmer les conditions contractuelles de l'entité du compte avant déploiement.
[MT5 — types d'ordres](https://www.metatrader5.com/en/terminal/help/trading/general_concept),
[Axi — délais et slippage](https://help.axi.com/en-US/axiv2--axicorp-prod/article/sJdiOjbr-why-am-i-experiencing-execution-delay-or-slippage-in-mt4mt5).

### VPS : disponibilité, pas achat de rentabilité

Axi annonce une demande soumise à revue, avec 15 lots le mois précédent **ou**
un démarrage avec dépôt minimal de 500 USD. Ce n'est pas une preuve d'éligibilité
du compte DEMO ni une raison de déposer/trader pour obtenir le service.
[Conditions Axi consultées le 06/09](https://help.axi.com/en-GB/axiv2--axicorp-prod/article/bCLcLfHf-does-axi-offer-a-free-vps-service).

ForexVPS Core affiche 2 CPU, 4 Go, 100 Go, Windows Server et sauvegardes,
avec 25,60 USD/mois **sur facturation annuelle de 307,20 USD**, offre publique
distincte du service Axi. Localisation/SLA effectif/rétention/restauration/taxes
et tarif de renouvellement restent à confirmer au devis. Ne pas assimiler
« 100 % garanti » à une absence physique de panne.
[Offre du fournisseur](https://www.forexvps.net/forex-vps-hosting/).

L'AWS 2 Go existant n'a pas été inspecté : OS/CPU/charge/coût restent inconnus.
Il peut héberger la supervision ; je n'y migre pas MT5+LLM+rejeux sans benchmark.
Préparation proposée : Windows x64, Python signé, copie de release vérifiée,
restauration SQLite testée hors connexion, essais sans armement, puis bascule
explicite à écrivain unique. La création de compte et toute dépense restent à Florent.

## Validation et suite

Commandes :

```powershell
.venv/Scripts/python.exe -m pytest tests/ -q --disable-warnings
& 'C:\Program Files\Git\bin\bash.exe' tools/lint_gate.sh
node .gitnexus/run.cjs detect-changes --scope all --repo titanium-v14
git diff --check
```

La commande courte `bash` est absente du PATH de cette session ; le même script
de lint est exécuté avec Git Bash installé. Aucune porte de sûreté n'a été assouplie.
Résultat final : **2 557 passed, 2 skipped, 71 subtests passed**, 19 avertissements
de catalogue de modèles, 145,62 s. Skips : dépendance optionnelle `langchain_aws`
absente et test réseau DeepSeek sans clé. Lint commun et `git diff --check` verts.
Hooks de commit : 28 tests de sûreté verts, sans contournement.

Commits livrés : `db398d3` (télémétrie/IRM), `aae766c` (audit/BH/preuve).
Le présent document et le correctif de synchronisation forment le lot workflow.
Retour arrière : `git revert <commit>` sur le lot concerné, puis mêmes tests,
sans reset du dépôt ni changement des journaux ; aucun redémarrage automatique.
Pour reproduire les rapports de portée historiques :

```powershell
node .gitnexus/run.cjs detect-changes --scope compare --base-ref 43bf65f --repo titanium-v14
git diff 43bf65f..HEAD -- tools/rejeu_univers.py titanium/backtest.py titanium/data/archive_barres.py titanium/edge.py titanium/features/builder.py titanium/features/candlesticks.py titanium/features/indicators.py titanium/features/smc.py titanium/features/structure.py titanium/features/ict_structure.py titanium/gates/confluence_gate.py
```

La seconde commande doit rester vide pour ce lot. Le hub canonique MCP 8770
répond ; le frontal 8097 est inaccessible au contrôle. Le relais est publié
directement sur le hub, sans créer un nouveau canal ni démarrer son interface.
Les fichiers de consigne non suivis et `.vscode/` appartiennent à l'utilisateur
et restent hors des commits de ce lot.

Incident d'index résolu sans supprimer de données métier :

```powershell
node .gitnexus/run.cjs analyze --force --skip-skills --index-only
powershell -NoProfile -ExecutionPolicy Bypass -File tools/gitnexus_team.ps1 sync
```

Reconstruction réussie en 25,2 s, synchronisation suivante réussie avec zéro
fichier de code changé ; index 11 369 nœuds / 22 407 relations / 300 flux.
Cette réparation restaure l'index courant, elle ne prétend pas corriger le
défaut interne du moteur GitNexus. Si l'incohérence FTS revient, ne pas exploiter
ses nombres de parcours avant reconstruction. Le catalogue reste à 49 skills.

**Prochain point humain :** programmer le chargement des corrections et la remise
en service des collecteurs en DEMO. Pas d'armement ni d'ordre de démonstration
dans ce mandat. Le chantier suivant est la chaîne intention–deals complète,
avant toute promotion d'une tactique de sortie ou augmentation du risque.
