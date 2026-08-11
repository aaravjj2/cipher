#!/usr/bin/env node
/** Turn a password into the CIPHER_APP_PASSWORD_HASH value, reading it from stdin.
 *
 * stdin rather than an argument on purpose: an argv password is visible in `ps` output
 * and lands in shell history, and this is the one credential a person types by hand.
 *
 *   printf '%s' "$PASSWORD" | node scripts/set-app-password.mjs
 *
 * Prints only the hash. Store that in Secret Manager as `cipher-app-password-hash`
 * (infra/gcp-cipher-vm/bin/sync-secrets.py materializes it into /etc/cipher/cipher.env);
 * the plaintext is never written anywhere by this script.
 */
import { hashPassword } from "../app/auth.mjs";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
// Trailing newlines come from the shell, not the user, so they are not part of the
// password; interior whitespace is preserved because a passphrase may contain spaces.
const password = Buffer.concat(chunks).toString("utf8").replace(/\r?\n+$/, "");

if (!password) {
  process.stderr.write("No password on stdin. Pipe one in; do not pass it as an argument.\n");
  process.exit(2);
}
// Short passwords are the failure this gate cannot survive, since the hash is the only
// thing between the open internet and live market data once the port is published.
if (password.length < 12) {
  process.stderr.write(`Password is ${password.length} characters; use at least 12.\n`);
  process.exit(2);
}

process.stdout.write(await hashPassword(password) + "\n");
