# Correction de couverture multi-actifs — 7 septembre 2026

## Perimetre reel, sans ajout de Grok

Le catalogue lu sur le terminal MT5 deja ouvert contient **149 instruments**.
Ce nombre est un constat, pas un plafond : les tests couvrent aussi un catalogue
de 173 instruments. Aucun nouveau fournisseur IA n'est ajoute.

| Classe resolue | Instruments |
|---|---:|
| FX | 74 |
| Metaux | 7 |
| Energie | 5 |
| Indices | 30 |
| Agricole | 3 |
| Crypto | 30 |

La verification du catalogue ne lit ni cle, ni mot de passe, ni `.env`, et ne
passe aucun ordre. La classification repose sur le resolveur existant et les
groupes du courtier; elle n'est pas une preuve de rentabilite ni d'ouverture.

## Corrections implementees

- Le playbook Hermes utilise maintenant `asset_class_of`, comme les moteurs,
  et non uniquement les 30 noms de la liste historique `ASSET_CLASSES`.
  Cela corrige notamment le suivi des positions sur les symboles non listes.
- Ajout du contexte agricole (publications datees, meteo, saisonnalite,
  echeance et roulement du contrat). Playbook versionne `v14-trading-playbook-2`.
  Une classification inconnue reste explicite et les consignes imposent WAIT.
- L'archiveur de quotes prend par defaut tout le catalogue, pas seulement les
  instruments visibles dans Market Watch. `--symboles` reste une selection
  explicite pour les campagnes ciblees; aucun filtre de risque n'est retire.
- L'audit dispose de `--univers-mt5` et d'un inventaire leger qui garde les
  instruments sans archive dans le denominateur. Le nom courtier `S&P.fs`
  est accepte sans autoriser les chemins traversants.

```powershell
.venv/Scripts/python.exe -X utf8 tools/audit_quotes_sorties.py `
  --univers-mt5 --inventaire --json results/couverture_univers_nouvelle.json
```

Sans `--inventaire`, chaque fichier est audite integralement. L'inventaire ne
lit que la fin du dernier fichier (64 Kio maximum par instrument), et ne
certifie ni la qualite de toutes les lignes ni la completude du flux.
Le terminal doit deja etre ouvert; un catalogue indisponible ne devient pas
silencieusement un sous-univers BTC/ETH. Le rapport JSON doit etre nouveau.

## Mesure de couverture, pas un feu vert d'execution

Preuve locale : `results/couverture_univers_20260907.json`.

- 141 instruments avec une archive, 8 sans archive.
- Derniere quote archivee au 24 aout pour 138 instruments, au 21 aout pour 3.
- Sans archive : USDUSC, GBCUSD, USDCAC, USDCHC, USDSGC, EUCUSD, USDJPC,
  MKR-USD. Les sept premiers sont desactives (`trade_mode=0`); MKR-USD est
  en cloture seule (`trade_mode=3`). Ce ne sont pas huit candidats d'entree.
- Catalogue global : 136 en mode complet, 5 en cloture seule, 8 desactives.
  Un mode complet ne garantit ni marche ouvert ni admissibilite au risque.

Le collecteur public BTC/ETH demeure un complement specialise. Il n'est ni
le catalogue de V14, ni un substitut aux quotes courtier des 149 instruments.
Les moteurs, l'archiveur MT5 et le collecteur L2 ne sont pas relances par ce lot.
Les futures relances doivent conserver le mur DEMO et les protections contre
les doublons; les anciennes politiques ne doivent pas etre presentees comme
produites avec le nouveau playbook.

## Verification

Les tests ajoutent un catalogue de 173 instruments, des symboles caches, six
classes hors liste historique, le contexte agricole, les instruments inconnus,
les archives manquantes, les chemins dangereux et le symbole `S&P.fs`.
Aucun des onze fichiers moteur scelles n'est modifie.

66 tests cibles passent; lint commun et lint strict des nouveaux modules et du
playbook passent. GitNexus upstream LOW sur chaque cible; impact agrege HIGH
(six parcours) car classification et archiveur sont partages. Les controles
ne suppriment aucun filtre d'entree existant.
La premiere suite complete a ete interrompue pendant les imports par Windows
(controle d'application sur une DLL Pandas), puis un nouvel import isole a
reussi sans changement des protections. La preuve initiale est conservee dans
`results/univers_complet_20260907_tests.log`; la relance est journalisee dans
`results/univers_complet_20260907_retry_tests.log`.
Verdict final de cette relance : **2664 tests passes, 2 ignores, 71 sous-tests
passes**, en 140,99 secondes.
