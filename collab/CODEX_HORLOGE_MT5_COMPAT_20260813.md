# Manifeste de compatibilite — horloge MT5

Date du contrat : 2026-08-13. Tache : `ba27713d`.

## Contrat courant

Les epochs `deal.time` recus du terminal Axi DEMO sont encodes dans l'heure du
serveur. Toute lecture nouvelle doit mesurer `server_offset_seconds`, puis
convertir chaque epoch une seule fois avec
`titanium.data.mt5_vendor.heure_serveur_en_utc`. Les objets produits par
`aggregate_mt5_deals` portent ensuite exclusivement des ISO UTC et ne doivent
plus etre recorriges par leurs consommateurs.

La mesure est explicite pour chaque lecture afin qu'un passage UTC+3 vers UTC+2
(ou l'inverse) ne change pas le contrat. Une mesure absente vaut 0 et reste
fail-closed : elle n'autorise aucune correction inventee.

## Cohorte historique immuable

`results/journal_rejets.ndjson` n'est pas reecrit. Au moment du manifeste, son
SHA-256 est
`4528F2B839AD71EB7CF8461F38470F80B87427412C3F43FED97C201CBBEB6622`.

Les 55 lignes ci-dessous ont `recovered_from_mt5_history=true` et
`horloge="utc"`, mais `ts_open` et `ts_exit` contiennent en realite l'heure
serveur Axi UTC+3 etiquetee `+00:00`. Pour une analyse temporelle seulement,
l'instant derive est donc `timestamp_stocke - 03:00`. Les montants, motifs et
identites restent inchanges. Cette regle ne doit jamais servir a modifier les
lignes sources ni a produire une nouvelle mesure d'edge.

Tickets manifestes (55) :

```text
86755928 86755936 86776744 86798362 86793654 86755945 86799230 86804282
86799226 86799231 86776753 86807839 86809844 86829601 86829608 86838629
86838641 86838663 86838673 86838703 86848465 86864813 86855532 86947649
86954494 86959955 86949539 86947661 86992822 86954508 86956388 86974012
87044922 87059822 87011717 86949532 87031360 87071031 87039165 87013676
87072518 86951058 87077066 87115756 87126637 87073995 87089192 87099119
87114927 87115743 87148498 87155772 87187455 87189245 89198681
```

Toute ligne append-only ecrite apres ce contrat avec `horloge="utc"` contient
du vrai UTC. Les analyses doivent distinguer les lignes nouvelles des 55
tickets manifestes ci-dessus ; le seul marqueur `horloge` ne suffit pas pour la
cohorte historique.
