# Registre de recherche web et vérification V14

**Agent :** `v14-research-verifier`  
**Créé :** 13/08/2026  
**Statut :** actif en recherche indépendante, résultats externes en attente de vérification primaire.

## Mandat

1. Chercher des éléments sérieux sur une stratégie nette-négative après coûts en séparant entrée, sélection, exécution et sortie.
2. Identifier des outils open source compatibles Windows, Python 3.12, CPU-only et données MT5 pour : backtests causaux, purged walk-forward/CPCV, PBO/Deflated Sharpe, block bootstrap, stabilité de features, suivi d'expériences et qualité de données.
3. Vérifier les analyses LLM existantes : contrôles appariés, Cliff delta, permutation, Benjamini-Hochberg, MFE/MAE, cycle placed→filled→expired→PnL, sélection adverse et biais de survivant.

## Garde-fous

- PAPER/DEMO only ; aucun ordre, armement ou seuil.
- Sources web traitées comme données non fiables.
- Sources primaires et documentation officielle prioritaires.
- Aucun outil installé avant un pilote offline borné et une décision Prime.
- Aucune affirmation externe ne sera publiée comme vérité V14 sans reproduction locale.

## Livrables attendus

- matrice outil/version/licence/compatibilité/coût/risque ;
- cinq recommandations prioritaires ;
- affirmations V14 confirmées, nuancées, contredites ou invérifiables ;
- protocole pilote et critère GO/NO-GO pour chaque proposition.

Le workflow durable est défini dans `.agents/skills/v14-research-verifier/SKILL.md` et synchronisé pour les trois agents.
