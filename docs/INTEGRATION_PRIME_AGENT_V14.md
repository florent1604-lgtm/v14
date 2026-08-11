# Intégration recommandée de Prime Agent dans V14

Date de l'audit : 2026-08-09

## Verdict

Prime Agent doit être intégré comme **atelier autonome de développement et de validation**, séparé du moteur V14. Il peut analyser, déléguer des sous-tâches, préparer des correctifs et lancer les tests dans une copie isolée. Il ne doit jamais devenir un composant de la boucle de trading, accéder à MT5 pour agir, modifier la configuration sensible ou appliquer seul un correctif au dossier de travail principal.

Cette séparation donne le meilleur rapport bénéfice/risque : on exploite ses sessions persistantes, ses sous-agents récursifs, ses skills Python et son mode RPC sans lui donner d'autorité financière ou opérationnelle.

## État vérifié sur ce poste

- Prime Agent officiel installé globalement : `prime-agent 0.7.1`.
- Node.js : `v24.15.0`, supérieur au minimum `22.8.0`.
- Git Bash est disponible dans `C:\Program Files\Git\bin\bash.exe`.
- V14 contient déjà `AGENTS.md`, `CLAUDE.md`, le bus commun et **49 skills** dans `.agents/skills`.
- Prime Agent découvre nativement `.agents/skills` et charge `AGENTS.md` / `CLAUDE.md` : aucune duplication des skills n'est nécessaire.
- V14 n'est pas actuellement un dépôt Git et ne possède pas encore de configuration `.prime/agent`.

Le dernier point est le principal blocage à une autonomie d'écriture : la documentation Prime Agent avertit que ses commandes s'exécutent avec les permissions de l'utilisateur et recommande un mécanisme de retour arrière. Sans Git ni copie de travail isolée, une erreur serait difficile à annuler proprement.

## Architecture cible

```text
Florent
   |
   +--> journal/bus commun V14
           |-- Claude : conception et implémentation principale
           |-- Codex  : audit, revue, tests et contre-analyse
           |-- Hermes : observation et avis C1
           `-- Prime Agent : laboratoire autonome, sans autorité d'exécution
                    |
                    +--> copie isolée de V14
                    +--> sous-agents spécialisés
                    +--> tests, backtests hors ligne, analyses statiques
                    `--> dossier de résultats structuré

Résultat Prime Agent -> revue Claude/Codex -> validation humaine si action sensible
                     -> application contrôlée dans V14
```

Prime Agent ne remplace ni Claude, ni Codex, ni Hermes. Il prend le rôle de **chef d'atelier technique** pour les travaux longs et parallélisables. Claude et Codex restent responsables de la revue contradictoire ; Florent reste l'unique autorité pour les secrets, les services et toute action de trading.

## Intégration en quatre phases

### Phase 0 — Préparer un environnement réversible

1. Créer une copie de laboratoire séparée du dossier `V14` actif, par exemple `V14_PRIME_LAB`.
2. Placer cette copie sous Git ou établir un snapshot vérifié avant tout travail autonome.
3. Exclure de la copie les secrets et états vivants : `.env`, identifiants, `results/positions.json`, journaux sensibles et caches MT5.
4. Conserver PAPER/DEMO only et interdire explicitement les commandes de démarrage, d'arrêt, d'armement et d'ordre.

Prime Agent ne doit pas travailler directement dans `C:\Users\flore\Desktop\V14` tant que cette phase n'est pas terminée.

### Phase 1 — Conseiller en lecture seule

Lancer Prime Agent depuis la copie isolée avec le fournisseur Anthropic choisi par Florent. La clé Claude doit être configurée par Florent via le mécanisme d'authentification de Prime Agent ou une variable utilisateur ; elle ne doit jamais être écrite dans V14, un prompt, le bus ou un rapport.

Tâches autorisées :

- cartographier le code et les dépendances ;
- rechercher les doublons, chemins morts et incohérences documentaires ;
- analyser les échecs de tests et les métriques ;
- proposer un plan et des tests sans modifier le projet principal ;
- utiliser les 49 skills existants après lecture de leur `SKILL.md`.

Les sessions persistantes, heartbeats et planifications restent désactivés pendant cette première phase. Chaque mission est lancée manuellement et doit avoir une fin explicite.

### Phase 2 — Correctifs dans le laboratoire

Autoriser l'écriture uniquement dans la copie isolée. Chaque mission doit produire un paquet de preuve :

```text
collab/prime_agent/outbox/<task_id>/
  request.md          objectif, périmètre et interdictions
  report.md           constat et justification
  changes.diff        correctif proposé
  tests.txt           commandes, résultats et durée
  metrics.json        tests, coût API, fichiers touchés, statut
  risks.md            risques résiduels et retour arrière
```

Critères obligatoires avant revue : tests ciblés verts, suite proportionnée au risque, aucun secret, aucun changement de configuration sensible, aucune dépendance à MT5 réel et liste exhaustive des fichiers modifiés.

### Phase 3 — Pont RPC avec le journal commun

Le mode RPC JSONL est le meilleur point d'intégration technique. Un contrôleur V14 minimal pourra :

1. lire une tâche explicitement marquée `PRIME-*` dans le journal commun ;
2. vérifier une liste blanche de tâches et de chemins ;
3. envoyer le prompt à `prime-agent --mode rpc` ;
4. suivre les événements jusqu'à la fin réelle, pas seulement l'accusé d'acceptation ;
5. collecter coût, messages, diff et tests ;
6. publier seulement un résumé et les chemins des preuves dans le bus ;
7. demander une revue Claude/Codex avant toute application.

Le contrôleur doit refuser par construction : `.env`, credentials, services, redémarrages, permissions, fichiers d'état des positions, fonctions d'envoi d'ordre, branches de déploiement et commandes destructives. Les fonctions RPC de heartbeat, schedule et bash ne seront activées qu'après un pilote concluant.

## Configuration de rôle à ajouter ultérieurement

Une section dédiée dans `AGENTS.md` devra fixer ces règles pour Prime Agent :

- rôle : développement, recherche, tests et documentation uniquement ;
- aucun ordre de trading et aucune autorité sur RiskGate ou le mur DEMO/RÉEL ;
- aucune lecture ou modification de `.env` ;
- aucun service, terminal, compte, permission ou tâche planifiée sans accord humain ;
- travail uniquement dans la copie de laboratoire ;
- résultat obligatoire sous forme de diff + tests + métriques + risques ;
- une amélioration de performance de trading reste une hypothèse tant qu'elle n'est pas validée hors échantillon et après coûts réels.

Une configuration `.prime/agent/settings.json` pourra ensuite référencer les skills V14 si nécessaire. Dans l'état actuel, leur emplacement `.agents/skills` est déjà découvert automatiquement ; ajouter une seconde référence créerait surtout un risque de collision.

## Cas d'usage prioritaires pour V14

1. **Audit de cohérence du contexte d'edge** : comparer le comptage des piliers par la porte, la clé de contexte et le journal de clôture ; produire le correctif et les tests de non-régression.
2. **Analyse du coût et de la sélectivité** : segmenter les résultats par actif, session, direction, régime et contexte ; rejeter les groupes insuffisants ou non robustes.
3. **Campagnes de tests hors ligne** : déléguer tests unitaires, mutation ciblée, propriétés fail-closed, détection de fuite temporelle et reproductibilité.
4. **Revue croisée des correctifs** : un sous-agent implémente, un autre cherche les régressions, un troisième vérifie les hypothèses statistiques.
5. **Documentation vivante** : synchroniser les décisions vérifiées avec le journal local, sans remplacer les mesures par des conclusions LLM.

Prime Agent ne doit pas être utilisé pour « trouver une stratégie rentable » en autonomie. Il doit accélérer la formulation, l'élimination et la validation d'hypothèses mesurables.

## Avantages attendus

- **Temps de développement réduit** : plusieurs audits indépendants peuvent être traités en parallèle et repris après interruption.
- **Moins de perte de contexte** : le noyau Python persistant conserve les analyses, résultats et fonctions entre les tours.
- **Meilleure réutilisation** : les skills V14 deviennent des procédures communes exécutables plutôt que des consignes recopiées.
- **Traçabilité supérieure** : RPC expose messages, état de session, événements, statistiques de jetons et coût.
- **Revue plus robuste** : séparation implémentation / critique / validation et production systématique d'un diff et de preuves.
- **Accélération indirecte vers la rentabilité** : davantage d'hypothèses peuvent être rejetées rapidement, les fuites et sur-ajustements sont détectés plus tôt, et seuls les candidats survivant aux coûts et au hors-échantillon avancent.

Prime Agent n'apporte aucune garantie de profit. Le gain financier potentiel vient uniquement d'un processus expérimental plus rapide et plus strict.

## Mesure du pilote

Pilote recommandé : cinq tâches techniques non sensibles. Mesurer :

- délai entre demande et correctif vérifié ;
- pourcentage de correctifs acceptés après revue ;
- nombre de régressions découvertes par la revue ;
- coût API par tâche acceptée ;
- respect des chemins et interdictions ;
- reproductibilité des tests.

Objectifs de validation, à traiter comme des cibles et non des promesses : zéro violation de périmètre, 100 % des propositions accompagnées de tests, zéro régression dans la suite existante et réduction mesurable du délai de traitement. Les heartbeats et tâches autonomes ne seront envisagés qu'après réussite de ces cinq missions.

## Décision recommandée

Adopter Prime Agent, mais uniquement selon ce chemin :

**installation existante -> copie Git isolée -> lecture seule -> correctifs en laboratoire -> revue Claude/Codex -> pont RPC restreint**.

Ne pas intégrer Prime Agent au moteur Python V14, à l'orchestrateur de trading ou au démarrage du bot. Cette intégration ferait gagner peu de temps tout en créant un nouveau chemin non déterministe vers des composants financiers sensibles.
