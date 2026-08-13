# Où est la perte : à l'entrée, pas à la sortie

**Claude · 13/08/2026** · 44 clôtures, compte DEMO 10055401 · lecture seule.

---

## Le résultat en une ligne

**La gestion de sortie fonctionne. C'est l'entrée qui ne fonctionne pas.**

```
motif de sortie     n    espérance      cumul
─────────────────────────────────────────────
init (stop plein)  26   −0.9763R    −25.38R
breakeven          10   −0.0312R     −0.31R
trailing            8   +1.0848R     +8.68R
```

Les huit sorties en trailing sont **toutes positives** :
`+1.33 +0.67 +0.43 +2.03 +0.75 +2.00 +0.90 +0.55`.

Quand un trade atteint le trailing, il rapporte en moyenne **+1,08 R**. Le
breakeven, lui, fait exactement son travail : dix trades ramenés à zéro net.
La perte totale de −17,02 R vient **entièrement** des 26 stops pleins.

---

## Pourquoi les stops pleins ne sont pas un problème de gestion

Des 27 pertes pleines, voici jusqu'où le prix était allé en leur faveur avant
de mourir :

| excursion favorable maximale | n | part |
|---|---|---|
| **jamais favorable (< +0,05 R)** | **12** | **44,4 %** |
| +0,05 à +0,2 R | 3 | 11,1 % |
| +0,2 à +0,5 R | 6 | 22,2 % |
| +0,5 R et plus | 6 | 22,2 % |

**Plus de la moitié des pertes pleines (55 %) n'ont jamais montré la moindre
excursion favorable au-delà de +0,2 R.** Le prix est parti contre la position
dès l'entrée et n'est jamais revenu.

Aucun réglage de sortie ne récupère ces trades. Un breakeven, quel que soit
son seuil, ne s'arme que si le prix va d'abord dans le bon sens. Sur 12
trades, il n'y avait rien à armer.

---

## Ce que ça change pour la tâche 65541466

La question « à quel seuil armer le breakeven » portait sur un levier qui,
dans le meilleur des cas, concerne **10 trades sur 27** — ceux montés à
+0,3 R ou plus. Et le prix de ce levier est prélevé sur la seule branche
rentable du système : les sorties en trailing.

Le rejeu était déjà invalidé pour un défaut d'horloge. Cette mesure-ci, qui
ne dépend d'aucune datation (les excursions sont des amplitudes, pas des
instants), dit quelque chose de plus gênant : **même correctement mesuré, ce
levier ne peut pas redresser le système.** Il déplace au mieux quelques R
d'une colonne à l'autre.

---

## L'axe qui reste

Si 44 % des perdants ne vont jamais dans le bon sens, la question utile n'est
pas « quand couper », c'est **« pourquoi entre-t-on »**.

Trois pistes mesurables, par ordre de coût :

1. **Comparer le contexte d'entrée des 12 « jamais favorables » à celui des 8
   trailing.** Le panel d'indicateurs est déjà journalisé à l'ouverture
   (`excursions.ndjson`, champ `indicators`, ~50 séries). Personne ne l'a
   encore exploité. C'est de la mesure pure, sans risque, sur des données
   déjà collectées.

2. **Regarder si les 12 partagent une famille, une classe d'actif, une
   session ou un nombre de piliers.** `context_key` et `support_pillars` sont
   au journal.

3. **Vérifier si le sens du setup est simplement inversé sur certains
   contextes.** Un contexte dont l'espérance est franchement négative sur un
   échantillon suffisant vaut mieux inversé que supprimé — mais cela demande
   20 clôtures par contexte, et on n'en a pas encore.

La piste 1 est celle que je recommande : elle utilise des données déjà
acquises, ne touche à aucun seuil, et répond à la seule question dont la
réponse peut changer l'espérance du système.

---

## Réserves

**44 clôtures.** Les huit sorties trailing tiennent le résultat positif à
elles seules ; retirer les deux plus grosses (+2,03 et +2,00) ferait passer
la branche trailing de +8,68 R à +4,65 R — toujours positive, mais l'écart
montre la fragilité de l'échantillon.

**Les excursions sont fiables ici**, contrairement au rejeu de breakeven :
`mae_r` et `mfe_r` sont des amplitudes mesurées au fil de l'eau par le
gestionnaire de positions, pas des croisements d'horodatages. Le défaut
d'horloge du 12/08 ne les affecte pas.

**Un trade sur 45 manque au journal** (EURAUD 89198681, ouvert et fermé dans
la même seconde, −22,48 EUR). Déjà fiché en `cb599499`. Sans effet sur les
proportions ci-dessus.
