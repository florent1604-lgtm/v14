#!/usr/bin/env node
/**
 * Sortie 0 si l'abonnement Claude Pro/Max est connecte, 1 sinon.
 *
 * Un `/login` reussi ecrit dans auth.json une entree "anthropic" de type
 * "oauth", prioritaire sur toute variable d'environnement. Une entree de type
 * "api_key" n'est PAS un abonnement : elle ne declenche pas la bascule.
 *
 * Ce script ne lit, n'affiche et ne journalise aucune valeur secrete : il ne
 * regarde que le champ `type`.
 *
 * Usage : node tools/prime_auth_probe.mjs <chemin/auth.json>
 */
import { readFileSync } from "node:fs";

const chemin = process.argv[2];
if (!chemin) process.exit(1);

try {
  const donnees = JSON.parse(readFileSync(chemin, "utf8"));
  const entree = donnees?.anthropic;
  process.exit(entree && entree.type === "oauth" ? 0 : 1);
} catch {
  process.exit(1);
}
