# Recherche web indépendante — rentabilité, outils et validation V14

**Mandat Hermes · 13/08/2026 · recherche documentaire, PAPER/DEMO only**

## Synthèse indépendante — Titanium V14

**Périmètre respecté :** recherche documentaire uniquement, aucune modification de fichier, aucun accès au compte MT5, aucun ordre réel ou démo envoyé.  
**État des sources vérifié le 13 août 2026.**

## Conclusion générale

La littérature soutient la décision actuelle de **ne rien promouvoir**. Avec une performance nette négative, des coûts incomplets, une cohorte UTC encore petite et un taux d’exécution des limites proche de 29 %, V14 ne dispose pas encore d’une preuve robuste d’edge économique.

L’ordre rationnel d’investigation est :

1. reconstruire exactement les coûts et le cycle de vie des ordres ;
2. mesurer l’edge d’entrée avant toute logique de sortie ;
3. isoler la valeur ajoutée de la sélection/confluence ;
4. évaluer séparément l’exécution ;
5. seulement ensuite tester les sorties et le dimensionnement.

Ni CPCV, ni PBO, ni Deflated Sharpe ne peuvent « sauver » une stratégie nette négative. Ces méthodes servent à réduire le risque de faux positif, pas à créer un edge.

---

# 1. Diagnostic d’une stratégie nette négative après coûts

## 1.1 Décomposition recommandée

Pour chaque signal, conserver un prix de décision causal \(p_0\), puis calculer quatre étages distincts.

| Étage | Question | Mesure principale | Interprétation |
|---|---|---|---|
| **Edge d’entrée** | Le marché évolue-t-il dans le bon sens après le signal ? | rendements signés bruts à horizons fixes, MFE/MAE depuis \(p_0\) | Négatif ici : problème de signal, pas d’exécution |
| **Sélection** | La confluence/RiskGate choisit-elle de meilleurs signaux que ceux qu’elle rejette ? | contraste accepté vs contrôle apparié/rejeté, hors échantillon | Gain brut sans gain net : sélection trop faible pour les coûts |
| **Exécution** | Les ordres obtiennent-ils un meilleur prix sans sélectionner les pires trajectoires ? | implementation shortfall, spread, slippage, fill, markout après fill/expiration | Fill faible et markout négatif : sélection adverse probable |
| **Sorties** | La sortie extrait-elle l’edge disponible ? | PnL réel vs sorties contrefactuelles prédéfinies | MFE élevé ne prouve pas qu’une sortie réalisable pouvait le capturer |

La référence conceptuelle classique pour mesurer le coût entre la décision et l’exécution est l’**implementation shortfall** :

- Perold, *The Implementation Shortfall*, 1988.  
  DOI : https://doi.org/10.3905/jpm.1988.409150

### Test pilote borné

Sur la cohorte UTC propre, sans modifier la stratégie :

- figer les horizons, par exemple 1/3/5/10 barres ;
- calculer :
  - PnL brut au prix de décision ;
  - PnL après spread ;
  - après commission ;
  - après slippage/fill observé ;
  - après sortie réelle ;
- produire la décomposition par actif, session et régime de volatilité.

**GO diagnostic :** au moins un horizon possède un effet brut directionnel stable sur les folds temporels, puis reste positif avec une enveloppe de coûts conservatrice.  
**NO-GO :** edge brut nul/négatif, ou avantage inférieur au coût médian plus marge d’incertitude.

---

## 1.2 Séparer entrée et sortie

Une sortie complexe peut masquer une mauvaise entrée ; inversement, une mauvaise sortie peut détruire un edge brut réel. Il faut comparer les entrées V14 avec des sorties **prédéfinies avant l’analyse** :

- sortie après horizon fixe ;
- stop/target en unités de volatilité ;
- sortie temporelle ;
- sortie V14 actuelle.

Même entrée et même échantillon dans toutes les variantes.

### Risques

- choisir a posteriori l’horizon qui maximise le résultat ;
- utiliser le high/low d’une barre avant que cette barre soit entièrement observable ;
- supposer qu’un target touché avant un stop est exécutable quand les deux sont atteints dans la même barre ;
- comparer une sortie V14 sur les seuls trades remplis avec un benchmark sur tous les signaux.

**Pilote :** quatre sorties fixées, évaluées sur les mêmes folds purgés.  
**GO :** la sortie V14 bat les sorties simples sur plusieurs folds après coûts et correction de multiplicité.  
**NO-GO :** gain limité à une variante, un actif ou une période.

---

# 2. Vérification des analyses méthodologiques annoncées

## 2.1 Contrôles appariés même actif/heure/volatilité

### Verdict : **confirmé, mais à renforcer**

L’appariement sur actif, heure et régime de volatilité est bien supérieur à un contrôle aléatoire global. Il réduit des confusions évidentes liées aux sessions, aux spreads et aux régimes.

Il ne rend toutefois pas automatiquement l’analyse causale. Il faut aussi contrôler ou documenter :

- direction long/short ;
- jour de semaine et proximité des rollovers ;
- spread et liquidité au moment de décision ;
- chevauchement des trades ;
- durée ou horizon d’évaluation ;
- disponibilité effective des données ;
- événements macro si ceux-ci influencent l’univers ;
- sélection opérée par RiskGate en aval.

### Point important

Si les observations sont réellement appariées, l’inférence doit préserver les paires :

- permutation du label **à l’intérieur de chaque paire** ;
- ou statistique calculée sur les différences intra-paire ;
- et bootstrap par blocs de paires temporellement adjacentes.

Un Cliff delta standard entre deux échantillons indépendants ne tient pas pleinement compte de l’appariement.

**Pilote :** un contrôle unique prédéfini par signal, caliper de volatilité fixé, balance mesurée avant tout test.  
**GO :** déséquilibre standardisé faible sur les variables d’appariement et résultat stable avec plusieurs calipers raisonnables.  
**NO-GO :** nombreux signaux sans contrôle ou résultat très sensible au caliper.

---

## 2.2 Cliff delta + permutation + Benjamini–Hochberg

### Verdict : **bonne base, mais formulation actuelle à nuancer**

Cliff delta est une taille d’effet non paramétrique adaptée aux distributions asymétriques et aux valeurs extrêmes :

- Cliff, *Dominance Statistics: Ordinal Analyses to Answer Ordinal Questions*, 1993.  
  DOI : https://doi.org/10.1037/0033-2909.114.3.494

Benjamini–Hochberg contrôle le taux attendu de fausses découvertes dans une famille de tests sous indépendance ou certaines formes de dépendance positive :

- Benjamini & Hochberg, 1995.  
  DOI : https://doi.org/10.1111/j.2517-6161.1995.tb02031.x

Implémentation maintenue :

- statsmodels 0.14.6, `multipletests(method="fdr_bh")` :  
  https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html
- version PyPI 0.14.6, Python ≥3.9, projet actif au 12 août 2026.

### Corrections nécessaires

1. **Cliff delta ne remplace pas un intervalle d’incertitude.** Fournir un intervalle par bootstrap bloc/paires.
2. **Permutation i.i.d. interdite** si les observations se chevauchent ou sont autocorrélées.
3. Avec un appariement, permuter les signes/désignations à l’intérieur des paires.
4. Définir la famille BH avant de regarder les résultats :
   - par exemple tous les piliers × actifs × horizons d’une analyse ;
   - ne pas corriger séparément chaque petit tableau pour obtenir plus de résultats.
5. Les tests actifs/horizons voisins sont dépendants. BH peut rester raisonnable sous dépendance positive, mais une analyse de sensibilité avec Benjamini–Yekutieli ou une hiérarchie actif → horizon est souhaitable.

**GO :** taille d’effet économiquement pertinente, intervalle ne couvrant pas essentiellement zéro, résultat stable après permutation bloquée et correction FDR.  
**NO-GO :** seulement un p-value ajusté significatif sans taille d’effet stable.

---

## 2.3 Comparaison MFE/MAE

### Verdict : **confirmé comme diagnostic, contredit comme preuve autonome d’une meilleure sortie**

MFE et MAE sont utiles pour déterminer :

- si l’entrée offre une excursion favorable avant l’excursion défavorable ;
- si les stops sont systématiquement dans le bruit normal ;
- si les sorties abandonnent un gain déjà disponible ;
- si remplis et non-remplis connaissent des trajectoires différentes.

Mais MFE/MAE utilisent des extrema futurs et sont donc **descriptifs ex post**. Un MFE élevé ne démontre pas qu’un take-profit causal pouvait être exécuté :

- ordre intra-bar non observable ;
- séquence high/low inconnue ;
- spread et latence ;
- prix touché sans volume disponible ;
- optimisation a posteriori du seuil.

### Pilote

Mesurer MFE/MAE dans une fenêtre fixée :

- depuis le prix de décision pour tous les signaux ;
- depuis le prix de fill pour les ordres remplis ;
- séparément pour placed, filled et expired ;
- en unités de spread et de volatilité.

**GO :** structure MFE/MAE reproductible hors échantillon et transformable en règle prédéfinie qui améliore le PnL net dans un replay causal conservateur.  
**NO-GO :** avantage uniquement visible sur les extrema ex post.

---

## 2.4 Pipeline limites placed → filled → expired → PnL

### Verdict : **fortement confirmé ; priorité absolue**

Analyser seulement le PnL des ordres remplis conditionne sur un événement dépendant de la trajectoire future. C’est précisément là que peut apparaître la **sélection adverse** :

- les ordres limites se remplissent davantage quand le marché se déplace contre eux ;
- les ordres qui auraient été très profitables peuvent ne jamais revenir jusqu’à la limite ;
- un fill rate de 29 % n’est donc ni bon ni mauvais isolément.

Le funnel minimal doit être :

1. signal éligible ;
2. ordre décidé ;
3. ordre placé/rejeté techniquement ;
4. temps en attente ;
5. partiellement ou totalement rempli ;
6. expiré/annulé ;
7. prix et coût du fill ;
8. markout après 1/3/5/10 barres ;
9. PnL réalisé ;
10. PnL/opportunity cost du non-fill.

### Comparaisons indispensables

- PnL de **tous les ordres placés**, avec règle prédéfinie pour les non-fills ;
- markout des fills relativement :
  - au prix de décision ;
  - au mid au moment du fill ;
  - à une exécution marché contrefactuelle conservatrice ;
- trajectoire après expiration ;
- distribution conditionnelle du fill selon distance à la limite, spread, volatilité et délai.

### Pilote

Replay hors ligne sur tous les ordres limites historiques disponibles, sans envoyer d’ordre.

**GO :** gain de prix obtenu par la limite supérieur au coût cumulé de sélection adverse et d’opportunité, avec résultat robuste par actif/session.  
**NO-GO :** markout post-fill défavorable et/ou PnL « intention-to-trade » inférieur à une exécution marché conservatrice.

---

## 2.5 Biais de survivant

### Verdict : **confirmé et potentiellement sous-estimé**

Le biais ne concerne pas seulement les actions radiées. Pour V14, il peut provenir de :

- symboles actuellement disponibles chez le broker seulement ;
- périodes avec données complètes seulement ;
- exclusion silencieuse des ordres rejetés ou des timestamps invalides ;
- paramètres/piliers conservés après exploration ;
- actifs abandonnés après mauvais résultats ;
- cohorte UTC « propre » construite selon des critères influencés par les résultats.

Référence :

- Brown et al., *Survivorship Bias in Performance Studies*, 1992.  
  DOI : https://doi.org/10.1093/rfs/5.4.553

**Pilote :** manifeste de population comprenant chaque symbole, période, exclusion et motif avant calcul du PnL.  
**GO :** 100 % des lignes d’entrée sont comptabilisées comme incluses ou exclues avec motif stable.  
**NO-GO :** trous de données ou rejets disparaissant simplement de la cohorte.

---

# 3. Outils open source applicables

## 3.1 skfolio — walk-forward purgé et CPCV

- **Version vérifiée :** 0.20.1, publiée le 21 avril 2026.
- **Python :** ≥3.10, donc compatible Python 3.12.
- **Licence :** BSD-3-Clause.
- **Maintenance :** active, dernier push observé le 31 juillet 2026.
- **Documentation officielle :**  
  https://skfolio.org/user_guide/model_selection.html  
  https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html
- **Fonctions pertinentes :** `WalkForward`, `CombinatorialPurgedCV`, prédictions cross-validées.

### Utilité V14

- construire des folds temporels purgés ;
- éviter que les labels/trades chevauchants contaminent train et test ;
- obtenir plusieurs chemins OOS pour mesurer la dispersion ;
- base pratique pour CPCV/PBO.

### Dépendances/coût

NumPy, pandas, scikit-learn et dépendances de skfolio. CPU modéré pour un walk-forward simple ; CPCV peut devenir combinatoire.

### Risques

- bibliothèque orientée portefeuille : il faut vérifier que les indices de V14 et la durée réelle des labels sont correctement transmis ;
- le purge/embargo doit couvrir la fin effective du label ou du trade, pas un nombre arbitraire de lignes ;
- CPCV sans observation suffisante produit de nombreuses estimations corrélées mais peu informatives.

### Pilote

Un seul actif liquide, un horizon, 6–8 groupes temporels, maximum 20 chemins CPCV.

**GO :** aucun chevauchement train/test vérifié automatiquement et durée CPU <30 minutes sur Ryzen 7.  
**NO-GO :** API incapable d’exprimer les intervalles début–fin des labels ou résultats très sensibles au nombre de groupes.

---

## 3.2 `arch.bootstrap` — bootstrap par blocs

- **Version :** arch 8.0.0, publiée le 21 octobre 2025.
- **Python :** ≥3.10.
- **Maintenance :** active, dernier push le 10 août 2026.
- **Documentation officielle :**  
  https://bashtage.github.io/arch/bootstrap/bootstrap.html
- Fournit notamment bootstrap par blocs temporels, dont méthodes stationnaires/circular block.

Référence du stationary bootstrap :

- Politis & Romano, 1994.  
  DOI : https://doi.org/10.1080/01621459.1994.10476870

### Utilité V14

- intervalles de confiance pour PnL moyen/médian, Cliff delta et markouts ;
- préserver partiellement autocorrélation et clustering de volatilité ;
- bootstrap de journées/sessions entières ou de paires appariées.

### Coût

Faible à modéré ; 2 000–10 000 réplications sont raisonnables sur CPU si les statistiques sont vectorisées.

### Risques

- taille de bloc choisie après inspection ;
- bootstrap des trades individuels malgré leur chevauchement ;
- régime non stationnaire ;
- blocs coupant les paires ou les sessions.

### Pilote

2 000 réplications, trois tailles de bloc prédéfinies, statistique unique principale.

**GO :** conclusions stables entre tailles plausibles.  
**NO-GO :** signe ou intervalle dépend entièrement d’une taille de bloc.

---

## 3.3 statsmodels + SciPy — tests, permutation et FDR

- **statsmodels :** 0.14.6, Python ≥3.9, actif.
- **Documentation FDR :**  
  https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html
- **Licence :** BSD.
- SciPy fournit les primitives de permutation/bootstrap ; écrire une permutation bloquée spécifique à V14 restera probablement nécessaire.

### Utilité V14

- BH/BY ;
- statistiques robustes ;
- permutation au niveau paire/journée ;
- modèles simples pour fill probability ou PnL conditionnel.

### Coût

Faible ; permutation 10 000 fois sur de petits échantillons reste accessible sur CPU.

### Risques

Les fonctions génériques n’inventent pas la bonne unité de permutation. Une permutation ligne par ligne peut produire des p-values artificiellement petites.

### Pilote

Comparer permutation naïve, intra-paire et par bloc de journée sur une seule hypothèse.

**GO :** procédure reproductible avec seed et familles de tests figées.  
**NO-GO :** impossible de définir une unité d’échange valide.

---

## 3.4 Pandera — qualité des données

- **Version :** 0.32.1, publiée le 29 juin 2026.
- **Python :** ≥3.10.
- **Licence :** MIT.
- **Maintenance :** active, push le 7 août 2026 ; documentation marquée « Active ».
- **Documentation officielle :**  
  https://pandera.readthedocs.io/en/stable/

### Utilité V14

Schémas explicites pour :

- timestamps UTC, monotonie et unicité ;
- OHLC cohérent ;
- spread non négatif ;
- prix/volume/tick-size ;
- états d’ordres valides ;
- clé signal→ordre→deal ;
- absence de duplication ;
- motifs d’exclusion obligatoires.

### Coût

Faible sur la cohorte actuelle ; peut devenir notable sur des millions de ticks, où une validation échantillonnée ou par partitions sera préférable.

### Risques

- coercition silencieuse de types ;
- validation après nettoyage, masquant les erreurs brutes ;
- schémas trop permissifs ;
- changements d’API Pandera entre versions.

### Pilote

Valider un export historique figé avec 10–15 invariants critiques.

**GO :** détecte des corruptions synthétiques injectées et ajoute <10 % au temps du pipeline.  
**NO-GO :** conversions automatiques masquent les timestamps/prix invalides.

---

## 3.5 MLflow — suivi d’expériences hors ligne

- **Version :** 3.15.1, publiée le 3 août 2026.
- **Python :** ≥3.10.
- **Licence :** Apache-2.0.
- **Maintenance :** très active, push le 13 août 2026.
- **Documentation officielle :**  
  https://mlflow.org/docs/latest/ml/tracking/

MLflow enregistre paramètres, version du code, métriques et artefacts ; il peut fonctionner avec un stockage local, sans service cloud.

### Utilité V14

Tracer pour chaque analyse :

- hash du dataset/manifeste ;
- coûts utilisés ;
- univers et exclusions ;
- seed ;
- folds ;
- paramètres de purge/embargo ;
- nombre réel de variantes essayées ;
- PBO/DSR, PnL brut/net et funnel d’exécution.

### Coût

CPU faible, mais dépendances et occupation disque plus importantes que pour un journal CSV/SQLite artisanal.

### Risques

- lourdeur opérationnelle ;
- artefacts volumineux ;
- croire que le tracking garantit la reproductibilité sans versionner les données ;
- enregistrer des secrets MT5 : à interdire.

### Pilote

Backend local SQLite, 20 runs factices/historiques, aucun credential.

**GO :** un run est reproductible depuis son manifeste et retrouvable en moins de cinq minutes.  
**NO-GO :** installation/conflits > une demi-journée ou base locale instable.

---

## 3.6 scikit-learn — validation de features

- **Version vérifiée :** 1.9.0, Python ≥3.11.
- **Maintenance :** active.
- **Documentation officielle :**  
  Time-series CV : https://scikit-learn.org/stable/modules/cross_validation.html#time-series-split  
  Permutation importance : https://scikit-learn.org/stable/modules/permutation_importance.html

La documentation avertit explicitement que l’importance par permutation :

- mesure l’importance pour **un modèle donné**, pas la valeur intrinsèque d’une feature ;
- ne doit être interprétée qu’après validation du pouvoir prédictif hors échantillon.

### Utilité V14

- ablation d’un pilier ;
- permutation importance OOS ;
- baseline simple, régularisée ou arbre peu profond ;
- calibration et métriques de classement.

### Risques

- `TimeSeriesSplit` seul n’est pas purgé pour des labels chevauchants ;
- permutation individuelle détruit la structure temporelle ;
- features colinéaires se partagent ou masquent leur importance ;
- sélection de features répétée sur le test final.

### Pilote

Un modèle linéaire régularisé et une baseline constante, importance calculée fold par fold avec permutation en blocs.

**GO :** amélioration OOS stable et importance cohérente dans la majorité des folds.  
**NO-GO :** modèle ne bat pas la baseline ou importance change systématiquement de signe.

---

## 3.7 VectorBT — prototype de backtest, avec réserves

- **Version :** 1.1.0, publiée le 5 juillet 2026.
- **Python :** ≥3.11 et <3.15, donc Python 3.12 compatible.
- **Maintenance :** active, push le 2 août 2026.
- **Documentation :** https://vectorbt.dev/api/portfolio/base/

### Utilité V14

Très rapide pour :

- grilles de sorties fixes ;
- sensibilité aux coûts ;
- scénarios de prix prédéfinis ;
- comparaison massive de variantes sur données déjà causales.

### Risques majeurs

- modèle vectorisé susceptible de masquer l’ordre intra-bar ;
- simulation de limites insuffisante si seuls OHLC sont disponibles ;
- proximité d’une offre commerciale PRO ;
- licence et modules exacts à auditer avant intégration ;
- ne remplace pas le replay du cycle MT5 réel.

### Pilote

Rejouer 100 trades simples dont le résultat attendu est calculé manuellement, incluant barres ambiguës.

**GO :** égalité exacte avec les cas non ambigus et comportement conservateur documenté pour les ambiguïtés.  
**NO-GO :** fill optimiste ou séquence stop/target implicite non contrôlable.

---

## 3.8 MetaTrader5 Python

- **Version PyPI vérifiée :** 5.0.6090.
- **Python déclaré :** ≥3.6,<4, donc 3.12 accepté.
- **Documentation officielle :** https://www.mql5.com/en/docs/python_metatrader5

### Utilité V14

Uniquement pour extraction ou rapprochement hors ligne de données historiques :

- ordres ;
- deals ;
- symbol info ;
- ticks ;
- coûts disponibles.

### Risques

- API liée au terminal Windows ;
- historique dépendant du broker ;
- fuseaux et conventions de symboles ;
- données manquantes ou révisées ;
- fonctions d’envoi d’ordre présentes dans le même package.

### Pilote autorisé

Export en lecture seule depuis le compte démo, dans un processus où les fonctions d’envoi sont interdites/mokées.

**GO :** rapprochement exact ordres–deals et couverture temporelle documentée.  
**NO-GO :** timestamps/coûts non réconciliables ou risque de chemin d’exécution accidentel.

---

## 3.9 Outils à ne pas adopter immédiatement

### `mlfinlab`

- dépôt public non archivé mais dernier push observé le 2 octobre 2023 ;
- statut de licence/offre difficile à qualifier comme solution open source récente complète ;
- risque de dépendre d’implémentations commerciales ou anciennes.

**Décision : NO-GO par défaut**, sauf audit précis de chaque module et licence.

### `backtesting.py`

- version PyPI 0.6.6, Python ≥3.9 ;
- dépôt actif, mais licence **AGPL-3.0** ;
- moteur pratique pour prototypes OHLC, pas une preuve de réalisme des fills MT5.

**Décision :** acceptable seulement comme oracle secondaire sous audit de licence ; pas comme moteur d’exécution principal.

---

# 4. PBO et Deflated Sharpe

Références primaires :

- Bailey et al., *The Probability of Backtest Overfitting*, version journal 2016.  
  DOI : https://doi.org/10.21314/jcf.2016.322
- Bailey & López de Prado, *The Deflated Sharpe Ratio*, 2014.  
  DOI : https://doi.org/10.2139/ssrn.2460551

## Application à V14

### PBO

Pertinent si V14 possède une **matrice complète** :

- lignes : sous-périodes comparables ;
- colonnes : toutes les configurations effectivement essayées ;
- aucune suppression des mauvaises variantes.

Il estime la fréquence à laquelle le meilleur in-sample devient médiocre out-of-sample.

### Deflated Sharpe

Corrige notamment :

- multiplicité des essais ;
- sélection du meilleur Sharpe ;
- non-normalité des rendements.

### Limites

- une petite cohorte rend les deux métriques extrêmement instables ;
- sous-déclarer le nombre de variantes essayées rend le DSR trop optimiste ;
- PBO ne convient pas à une seule stratégie sans historique des alternatives ;
- les trades chevauchants réduisent fortement la taille effective de l’échantillon.

### Pilote

Conserver seulement un univers borné de variantes déjà évaluées, avec tous leurs résultats, et produire PBO/DSR comme **indicateurs de risque**, non comme tests de promotion.

**GO :** pipeline techniquement reproductible et matrice d’essais complète.  
**NO-GO :** historique des essais incomplet ou cohorte insuffisante pour former des partitions utiles.

---

# 5. Les cinq recommandations prioritaires

## 1. Reconstruire le ledger économique avant toute optimisation

Inclure tous les signaux, décisions, ordres, fills, expirations, rejets, coûts et non-fills. Calculer le PnL au prix de décision, brut, puis après chaque coût.

**Critère de sortie :** rapprochement exhaustif et coûts connus ou enveloppe conservatrice. Sans cela, toutes les conclusions restent provisoires.

## 2. Réaliser l’audit « edge brut → sélection → exécution → sortie »

Utiliser horizons fixes et sorties simples, mêmes événements et mêmes folds. Ne toucher à aucun paramètre V14 pendant cet audit.

**Décision :** si l’edge brut est négatif, arrêter l’optimisation d’exécution et de sorties ; revenir aux features/entrées.

## 3. Traiter les limites en intention-to-trade, pas seulement sur les fills

Analyser ensemble filled, expired et opportunity cost, avec markout post-fill. Le taux de 29 % seul n’est pas une métrique d’efficacité.

**Décision :** conserver les limites seulement si leur amélioration de prix excède sélection adverse et coût des non-fills.

## 4. Installer une validation temporelle purgée minimaliste

Pilote skfolio + `arch.bootstrap`, avec peu de folds et peu de chemins compte tenu de la petite cohorte. Garder un test temporel final jamais consulté.

**Décision :** ne pas calculer une « précision » factice de CPCV/PBO si le nombre effectif d’événements indépendants est trop faible.

## 5. Geler protocole, familles de tests et manifeste des données

Pandera pour les invariants ; MLflow local ou, si trop lourd, un manifeste SQLite/JSON équivalent. Enregistrer toutes les variantes, y compris les échecs.

**Décision :** aucune affirmation par actif/pilier/horizon sans famille BH prédéfinie, taille d’effet et intervalle par bootstrap bloc.

---

# 6. Statut des affirmations V14

| Affirmation V14 | Verdict documentaire |
|---|---|
| Les contrôles doivent être appariés même actif/heure/volatilité | **Confirmé**, mais ajouter spread, direction, calendrier et permutation intra-paire |
| Cliff delta est adapté aux PnL non normaux | **Confirmé**, avec intervalle bootstrap ; standard Cliff delta ne modélise pas l’appariement |
| Permutation + BH protège contre les faux positifs | **Nuancé** : seulement si permutation respecte paires/blocs et si la famille de tests est prédéfinie |
| MFE/MAE permet d’évaluer les sorties | **Confirmé comme diagnostic**, **contredit comme preuve causale autonome** |
| Le funnel placed→filled→expired→PnL est nécessaire | **Fortement confirmé** |
| Un fill rate de 29 % démontre une mauvaise exécution | **Contredit** : insuffisant sans markout et opportunity cost |
| Le PnL négatif des fills démontre que le signal est mauvais | **Contredit** : peut venir du signal, de la sélection adverse, des coûts ou des sorties |
| CPCV/PBO/DSR peuvent valider malgré une petite cohorte | **Contredit/fortement nuancé** : estimation probablement très incertaine |
| Les métaux/énergie exploratoires peuvent être promus si positifs | **Contredit** tant que multiplicité, coûts et échantillon OOS ne sont pas résolus |
| FX/indices ne battant pas les contrôles appariés ne doivent pas être promus | **Confirmé** |
| L’absence actuelle de promotion est justifiée | **Fortement confirmé** |

---

## Compte rendu opérationnel

- **Travail effectué :** consultation de documentations officielles, métadonnées PyPI/GitHub et papiers académiques sur validation temporelle, backtest overfitting, FDR, bootstrap, coûts d’exécution et biais de survivant.
- **Résultat principal :** priorité au ledger de coûts et au funnel complet des limites ; la petite cohorte ne permet pas encore une validation fiable par CPCV/PBO/DSR.
- **Fichiers créés ou modifiés par le sous-agent pendant sa recherche :** aucun. Cette synthèse a ensuite été publiée par Hermes dans `collab/HERMES_RECHERCHE_WEB_V14_20260813.md`.
- **Ordres envoyés :** aucun.
- **Difficultés :** certaines documentations commerciales/publiques de finance quantitative ont un statut de licence ou de maintenance insuffisamment clair ; elles ont donc été écartées des recommandations principales.