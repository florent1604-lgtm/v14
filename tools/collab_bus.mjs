#!/usr/bin/env node
/**
 * Journal de secours Claude <-> Codex <-> Hermes, compatible avec V12.
 *
 * Le canal principal reste CollabHub (MCP, port 8770). Ce fichier est le
 * repli local, append-only et lisible par un humain. Par defaut V14 reutilise
 * le flux V12 voisin afin de conserver un seul historique commun.
 */
import { appendFile, mkdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const siblingV12 = resolve(root, "..", "v12");
const configuredV12 = process.env.TITANIUM_V12_ROOT
  ? resolve(process.env.TITANIUM_V12_ROOT)
  : siblingV12;
const sharedRoot = existsSync(configuredV12) ? configuredV12 : root;
const overrideFile = process.env.COLLAB_BUS_FILE
  ? resolve(process.env.COLLAB_BUS_FILE)
  : null;
const streamFile = overrideFile || resolve(sharedRoot, "collab", "messages", "stream.ndjson");
const ackFile = overrideFile || resolve(sharedRoot, "collab", "messages", "acks.ndjson");

const PRINCIPALS = new Set(["claude", "codex", "hermes", "florent", "system"]);
const SECRET_PATTERNS = [
  /\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b/,
  /\b(?:api[_-]?key|password|passwd|secret|access[_-]?token)\s*[:=]\s*\S+/i,
  /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/,
];

function usage() {
  console.error([
    "Usage:",
    '  node tools/collab_bus.mjs send --from codex --to claude --task V14 --content "..."',
    '  node tools/collab_bus.mjs ack --from claude --for <id> --content "Recu."',
    "  node tools/collab_bus.mjs read --to hermes",
    "  node tools/collab_bus.mjs tail [--acks] [--limit 20]",
    "  node tools/collab_bus.mjs status",
  ].join("\n"));
  process.exitCode = 2;
}

function parseArgs(argv) {
  const values = { _: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) {
      values._.push(token);
      continue;
    }
    const name = token.slice(2);
    const next = argv[index + 1];
    if (next && !next.startsWith("--")) {
      values[name] = next;
      index += 1;
    } else {
      values[name] = true;
    }
  }
  return values;
}

function requireText(values, names) {
  for (const name of names) {
    if (typeof values[name] !== "string" || !values[name].trim()) {
      throw new Error(`Parametre manquant: --${name}`);
    }
  }
}

function normalizePrincipal(value, field) {
  const principal = String(value).trim().toLowerCase();
  if (!PRINCIPALS.has(principal)) throw new Error(`${field} inconnu: ${value}`);
  return principal;
}

function assertNoSecret(content) {
  if (SECRET_PATTERNS.some((pattern) => pattern.test(content))) {
    throw new Error("Message refuse: secret ou identifiant sensible detecte");
  }
}

async function append(path, value) {
  await mkdir(dirname(path), { recursive: true });
  await appendFile(path, `${JSON.stringify(value)}\n`, "utf8");
}

async function load(path) {
  try {
    const raw = await readFile(path, "utf8");
    return raw.split(/\r?\n/).filter(Boolean).flatMap((line) => {
      try { return [JSON.parse(line)]; } catch { return []; }
    });
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
}

const values = parseArgs(process.argv.slice(2));
const command = values._[0];

try {
  if (command === "status") {
    console.log(JSON.stringify({
      primary: "http://127.0.0.1:8770/mcp",
      hermes: "http://127.0.0.1:8766/mcp",
      fallback_root: sharedRoot,
      stream_file: streamFile,
      ack_file: ackFile,
      reuses_v12: sharedRoot.toLowerCase() === configuredV12.toLowerCase(),
    }, null, 2));
  } else if (command === "send" || command === "ack") {
    const content = values.content || values.body;
    requireText({ ...values, content }, ["from", "content"]);
    assertNoSecret(content);
    const from = normalizePrincipal(values.from, "Emetteur");
    const replyTo = values["in-reply-to"] || values.for || null;
    if (command === "ack" && !replyTo) throw new Error("Parametre manquant: --for");

    let to = values.to;
    if (!to && command === "ack") {
      const original = (await load(streamFile)).find((item) => item.id === replyTo);
      to = original?.from;
    }
    requireText({ to }, ["to"]);
    to = normalizePrincipal(to, "Destinataire");

    const message = {
      id: randomUUID(),
      type: command === "ack" ? "ack" : (values.type || "message"),
      from,
      to,
      task: values.task || "V14",
      in_reply_to: replyTo,
      ts_utc: new Date().toISOString(),
      content,
      body: content,
    };
    await append(command === "ack" ? ackFile : streamFile, message);
    console.log(JSON.stringify(message));
  } else if (command === "read") {
    requireText(values, ["to"]);
    const to = normalizePrincipal(values.to, "Destinataire");
    const messages = (await Promise.all([load(streamFile), load(ackFile)])).flat();
    console.log(JSON.stringify({ messages: messages.filter((item) => item.to === to) }));
  } else if (command === "tail") {
    const path = values.acks ? ackFile : streamFile;
    const limit = Math.max(1, Math.min(200, Number(values.limit || 20)));
    const messages = await load(path);
    console.log(messages.slice(-limit).map((item) => JSON.stringify(item)).join("\n"));
  } else {
    usage();
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
