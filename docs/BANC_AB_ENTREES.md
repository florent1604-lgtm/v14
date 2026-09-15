# Banc A/B des entrées V14

Ce banc est un outil d'analyse **PAPER/DEMO**. Il ne modifie ni la sélection des
signaux, ni le sizing, ni l'exécution MT5. Sa spécification préenregistrée est
`config/banc_ab_variantes.json` ; son SHA-256 doit être publié dans chaque
rapport.

## Contrat v1

La phase 1 ne voit que les champs ex ante et de résolution. Elle scelle une
cohorte homogène, calcule les effectifs et hache le masque. Toute tentative de
lire `pnl_r`, `mae_r`, `mfe_r` ou une autre issue lève une exception. La phase 2
n'est accessible que si l'intégrité, la maturation et la puissance sont
suffisantes.

L'ordre des états est : `ANALYSIS_BLOCKED`, `NOT_IDENTIFIABLE`, `NOT_POWERED`,
`EXPLORATORY_MEASURED`. Une décision encore ouverte bloque toute lecture
d'issue. Les cohortes `LIMITE` et `MARCHE`, ou deux epochs/configurations
différentes, ne sont jamais agrégées.

`H_quality` compare la moyenne non pondérée de `pnl_r` entre 4p
(`support_pillars=3`) et 3p (`support_pillars=2`). Son gate exige 125 décisions
4p, 814 décisions au total, 20 jours de décision et 30 symboles. Les analyses
secondaires sont désactivées en v1.

`B(r)` est seulement une sensibilité relative aux bornes 1,35 et 2,81, avec un
dénominateur égal au nombre de décisions gelées. Il n'y a ni normalisation par
les poids, ni réallocation, ni interprétation monétaire.

Le corpus historique de 373 décisions ne contient pas de sceaux complets de
configuration et de code. Le banc doit donc le signaler comme bloqué tant que
ces sceaux ne sont pas fournis ; il ne doit jamais les inventer.

## Exécution contrôlée

Le CLI dérive l'identité exclusivement du manifeste dont le SHA-256 figure dans
la spécification. Il vérifie le SHA-256 et le schéma de l'artefact, la
cardinalité, le cutoff et la cohérence du mode avant de construire le masque.
Le SHA canonique de la spécification doit aussi être fourni par une source
externe avec `--spec-sha256` : une spécification affaiblie et auto-signée est
donc refusée. Artefacts et manifestes sont chacun lus une seule fois ; le hash
et le parsing portent sur le même snapshot d'octets. L'ancien argument
d'identité fourni par l'appelant est volontairement supprimé.

Le SHA canonique actuellement préenregistré est
`7c0509562f0317f307ba58c40ce0a66cde072b8ab4b4434715722a40839517c0`.
Après toute modification explicitement autorisée de la spécification, il se
recalcule avant la mesure avec :

```powershell
.venv\Scripts\python.exe -c "from tools.banc_ab_entrees import spec_file_sha256; print(spec_file_sha256())"
```

```powershell
.venv\Scripts\python.exe tools\banc_ab_entrees.py `
  --cohort results\p1a\cohorte_373.json `
  --spec config\banc_ab_variantes.json `
  --spec-sha256 7c0509562f0317f307ba58c40ce0a66cde072b8ab4b4434715722a40839517c0 `
  --cutoff 2026-08-25T11:52:37Z `
  --output results\banc_ab_entrees.json --measure
```

Codes de sortie : `0` mesuré, `2` analyse bloquée, `3` non identifiable,
`4` puissance insuffisante. En cas de refus, le dernier rapport mesuré n'est
jamais écrasé : un fichier frère `.blocked.json`, `.not_identifiable.json` ou
`.not_powered.json` est écrit atomiquement.

Une mesure admissible publie l'IC bootstrap deux voies symbole × jour de
décision, LOSO, LODO, les folds calendaires avec purge exacte, la MDE et la
graine. Un nombre insuffisant de tirages bootstrap valides bloque la mesure ;
LOSO/LODO sont marqués fragiles si le quota d'omissions valides n'est pas
atteint. `B(r)` conserve sur chaque borne l'étiquette
`monetary_status=NOT_IDENTIFIABLE` et ne constitue qu'une sensibilité relative.

## Cohortes futures MARCHE et LIMITE

Le moteur journalise désormais chaque décision envoyée dans
`results/decision_registry.ndjson`, avec son identité de politique et son
horodatage exact pris avant l'envoi. `tools/sceller_decisions_entree.py` gèle ce
registre à un `decision_cutoff`, conserve les décisions encore ouvertes et
interdit le mélange de plusieurs epochs. Une décision ouverte bloque la lecture
des issues ; une limite expirée ou annulée n'est jamais transformée
silencieusement en trade à PnL nul.

L'empreinte du code est calculée une seule fois au démarrage sur l'inventaire
des producteurs de décision. Elle reste immuable pendant le processus : une
modification du working tree ne peut donc pas changer rétroactivement
l'identité d'un ordre. Ces garanties ne s'appliquent au processus actif qu'après
un redémarrage humainement autorisé ; ce document n'en constitue pas l'ordre.
