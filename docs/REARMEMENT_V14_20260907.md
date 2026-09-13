# Optimisations et rearmement DEMO — 7 septembre 2026

## Autorisation et perimetre

Florent a demande « continue les optimisation prioritaire et rearme v14 ».
Operation limitee au compte **10055401 / Axi-US50-Demo**, mode courtier DEMO.
Aucun changement de `.env`, de compte, de SL, de seuil de risque ou de moteur
scelle. Aucun effacement d'archive, de position ou de politique en memoire.
Le catalogue interroge comporte **149 instruments**, pas seulement BTC/ETH.
Les filtres existants de session, spread, risque et liquidite restent actifs.

## Lots integres

- `61ed5e7` — Codex : collectes publiques concurrentes, deux actifs maximum
  (douze appels HTTP maximum); publication des resultats dans l'ordre de fin.
  La periode est mesuree depuis le debut du cycle, sans ajouter cinq secondes
  apres un cycle deja lent. Les horodatages sources ne sont jamais rajeunis.
- `4c52241` — travail transmis par Claude, relu et teste : retrait de
  `jepa_latency_ms` de l'identite causale; conservation des vraies predictions
  JEPA. TTL par horizon : M1 60 s, M5 120 s, M15 225 s, H1 900 s, H4 3 600 s.
  L'expiration reste bornee par l'observation source, jamais par la reponse tardive.
- `64fe7a3` — diagnostic initial de Claude, durcissement Codex : les refus
  fournisseur sur stdout sont identifies meme apres une banniere ou dans un
  objet JSON d'erreur. Seule une categorie sans secret est journalisee, pas
  la sortie brute. Un refus « credit balance » ne prouve pas un solde epuise.
- `57cc1d7` — infrastructure de tests de Claude, revue Codex : exclusion du
  plugin LangSmith, substitut xxHash conditionnel **reserve aux tests**, surface
  reduite et avertissement visible en mode degrade. Etage Docker de tests fourni
  mais **non construit ni valide** ici. Le substitut ne valide pas la compatibilite
  des checkpoints avec xxHash natif; aucune securite Windows n'a ete desactivee.
- `d326eb4` — correction complementaire Codex : un `hermes-unavailable / WAIT / none`
  n'est plus un verdict cognitif definitif. Une seule nouvelle tentative est
  possible apres 600 s, avec budget conserve dans l'historique. Un vrai verdict
  ALLOW/WAIT/BLOCK reste definitif pour la meme identite; la fraicheur source est
  recontrolee avant l'appel. Aucune purge manuelle des anciennes politiques.

## Preuves et limites des mesures

La suite integree a donne **2 689 passed, 2 skipped, 71 subtests**, xxHash natif,
en 140,54 s; lint commun et tests cibles passent. Preuve locale :
`results/rearmement_integration_claude_20260907_tests.log`.
La correction de reprise bornee ajoute 12 tests : **2 701 passed, 2 skipped,
71 subtests**, xxHash natif, 138,91 s. Preuve finale :
`results/rearmement_retry_borne_20260907_tests.log`. Ses 72 tests cibles et le
lint passent aussi. Le worker analystes seul a ete recharge a 15:07:57 UTC,
sans nouvelle interruption du moteur; journal
`results/rearm_20260907T150757Z_analystes_retry.log`.

Diagnostic Hermes : JSON OK en **10,16 s**. Ce test technique n'est ni une mesure
de latence d'analyse de marche ni une garantie de disponibilite continue.
Nouvelle mesure de file : **672 lignes, 29 identites**, 134 lignes eligibles au
moment de leur demande avec les nouveaux TTL; horizons M15/H1/H4. La file est
vivante et contient deja des identites corrigees : ne pas presenter ces chiffres
comme une reduction de facturation observee de 95 %.

## Remise en service et verification

Relance cachee et controlee a **15:01 UTC**, apres validation compte et registre
d'execution. Arbres de processus precedents arretes, absence d'instances
survivantes verifiee, puis collecteur, dashboard, analystes et boucle `--armer`.
La surveillance publique planifiee a ete remise en marche. Les trois positions
preexistantes n'ont pas ete fermees par l'operation de relance.

Journaux : `results/rearm_20260907T150115Z_*.log`.
Verification a 15:04:56 UTC : moteur arme, 10 tours, 463 evaluations, **0 nouvel
ordre envoye**, zero incident d'etat; dashboard 8095 et metriques 9108 en HTTP 200.
Il s'agit de compteurs de cette relance, pas des performances historiques.

Les premiers refus cognitifs venaient de politiques `hermes-unavailable`, sans
modele valide. Leur absence d'autorite doit rester bloquante jusqu'a une reponse
Hermes valide, jamais etre contournee pour provoquer un trade.

La microstructure a retrouve trois places, mais une mesure ulterieure montre
BTC a 11,46 s et ETH a 6,93 s : **la fraicheur n'est pas garantie en permanence**.
Le seuil de 8 s continue de produire WAIT lorsque necessaire.

## Suite prioritaire

1. Mesurer le taux de reprises Hermes effectivement reussies et les refus
   fournisseur par categorie; ne pas acheter des credits sur une simple hypothese.
2. Mesurer p95/p99 de fraicheur publique et traiter les sources lentes sans
   relacher les seuils ni extrapoler une couverture publique aux 149 instruments.
3. Reprendre l'archivage bid/ask complet avec verrou mono-ecrivain et rattrapage
   borne avant de relancer l'ancien archiveur; il reste arrete actuellement.
4. Evaluer les executions nettes de frais, hors echantillon. Aucun resultat de
   rentabilite n'est etabli par ces corrections ou par le simple rearmement.
