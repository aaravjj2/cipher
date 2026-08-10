/** Single-user authentication for the browser application server.
 *
 * Cipher's app server binds 127.0.0.1 and, until now, had no notion of a user: the only
 * thing keeping 27 `/api/*` routes and the provider-key quota behind them private was
 * that nothing outside the tailnet could reach the port. Publishing the port removes
 * that, so the gate has to exist first.
 *
 * Two properties matter more than the mechanism:
 *
 *   1. It fails CLOSED. A reverse proxy (Tailscale Funnel, cloudflared) connects to
 *      127.0.0.1, so a remote request is indistinguishable from a local one at the
 *      socket level. There is therefore no safe way to infer "this one is local, skip
 *      the check" — an unconfigured server must refuse to serve rather than guess. The
 *      only way to run without a password is to say so out loud via CIPHER_APP_AUTH=off,
 *      which is what the test suite and local development do.
 *
 *   2. The session secret is DERIVED from the password hash rather than stored
 *      separately. That is not laziness: it means changing the password invalidates
 *      every outstanding session for free, and it removes a second secret that could
 *      drift out of sync with the first. `CIPHER_APP_SESSION_SECRET` can still override
 *      it when sessions need to survive a password rotation.
 *
 * Everything here is node:crypto — no dependencies are added to the app server.
 */
import { createHmac, randomBytes, scrypt as scryptCb, timingSafeEqual } from "node:crypto";
import { promisify } from "node:util";

const scrypt = promisify(scryptCb);

// N=16384 keeps one hash under ~17 MB, comfortably inside node's 32 MB scrypt maxmem
// default; maxmem is still passed explicitly so raising N later is a one-line change
// rather than a confusing runtime error. ~60ms per verify on this VM, which is the
// point — it is the difference between a stolen hash being crackable and not.
const SCRYPT = { N: 16384, r: 8, p: 1, keylen: 32, maxmem: 64 * 1024 * 1024 };
const HASH_SCHEME = "scrypt";

export const SESSION_COOKIE = "cipher_session";
// Long enough that a single user is not retyping a password all day, short enough that a
// cookie lifted off a machine they walked away from expires on its own.
export const SESSION_TTL_MS = 14 * 24 * 60 * 60 * 1000;

// Online guessing budget. Five wrong answers is far more than a person who knows the
// password needs, and the doubling lockout makes the sixth attempt onward worthless
// without ever locking the real user out for long.
const MAX_FAILS_BEFORE_LOCKOUT = 5;
const LOCKOUT_BASE_MS = 2000;
const LOCKOUT_CAP_MS = 15 * 60 * 1000;
const ATTEMPT_TTL_MS = 60 * 60 * 1000;

const b64url = (buffer) => Buffer.from(buffer).toString("base64url");

/** Compare two strings without leaking their contents through timing. */
function constantTimeEquals(a, b) {
  const left = Buffer.from(String(a ?? ""), "utf8");
  const right = Buffer.from(String(b ?? ""), "utf8");
  // timingSafeEqual throws on a length mismatch, and the length itself is not the
  // secret here, so comparing lengths first is safe and keeps the call from throwing.
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

/**
 * Hash a password for storage in CIPHER_APP_PASSWORD_HASH.
 * Format: scrypt$N$r$p$<salt base64url>$<key base64url> — self-describing, so a stored
 * hash stays verifiable after SCRYPT above is tightened.
 */
export async function hashPassword(password, salt = randomBytes(16)) {
  if (typeof password !== "string" || password.length === 0) {
    throw new Error("password must be a non-empty string");
  }
  const key = await scrypt(password, salt, SCRYPT.keylen, SCRYPT);
  return [HASH_SCHEME, SCRYPT.N, SCRYPT.r, SCRYPT.p, b64url(salt), b64url(key)].join("$");
}

/** Verify a password against a stored hash, using the parameters the hash records. */
export async function verifyPassword(password, stored) {
  if (typeof password !== "string" || typeof stored !== "string") return false;
  const parts = stored.trim().split("$");
  if (parts.length !== 6 || parts[0] !== HASH_SCHEME) return false;
  const [, rawN, rawR, rawP, rawSalt, rawKey] = parts;
  const N = Number(rawN);
  const r = Number(rawR);
  const p = Number(rawP);
  if (!Number.isInteger(N) || !Number.isInteger(r) || !Number.isInteger(p)) return false;
  let expected;
  let actual;
  try {
    expected = Buffer.from(rawKey, "base64url");
    actual = await scrypt(password, Buffer.from(rawSalt, "base64url"), expected.length, {
      N, r, p, maxmem: SCRYPT.maxmem,
    });
  } catch {
    // A malformed or absurdly-parameterized hash must read as "no", never as "yes".
    return false;
  }
  return expected.length > 0 && timingSafeEqual(expected, actual);
}

/**
 * Derive the session-signing key. Tied to the password hash by default so that a
 * password change revokes existing sessions without any extra bookkeeping.
 */
export function sessionSecretFor(passwordHash, override = "") {
  if (override) return Buffer.from(override, "utf8");
  return createHmac("sha256", String(passwordHash)).update("cipher-session-v1").digest();
}

/** Issue a signed session token. The expiry is inside the signed payload, not beside it. */
export function signSession(secret, { now = Date.now(), ttlMs = SESSION_TTL_MS } = {}) {
  const payload = b64url(JSON.stringify({ v: 1, exp: now + ttlMs }));
  const signature = b64url(createHmac("sha256", secret).update(payload).digest());
  return `${payload}.${signature}`;
}

/**
 * Validate a session token. Returns null for anything that is not a currently-valid
 * token — tampered, truncated, unsigned, or expired all look the same to the caller.
 */
export function verifySession(token, secret, { now = Date.now() } = {}) {
  if (typeof token !== "string" || !token.includes(".")) return null;
  const index = token.lastIndexOf(".");
  const payload = token.slice(0, index);
  const signature = token.slice(index + 1);
  const expected = b64url(createHmac("sha256", secret).update(payload).digest());
  // Signature first: never parse a payload that has not been authenticated.
  if (!constantTimeEquals(expected, signature)) return null;
  let claims;
  try {
    claims = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
  } catch {
    return null;
  }
  if (!claims || typeof claims.exp !== "number" || claims.exp <= now) return null;
  return claims;
}

/** Per-client failed-attempt tracking. In-memory is right for a single-user server. */
export function createAttemptLimiter({ now = () => Date.now() } = {}) {
  const attempts = new Map();

  const prune = (currentTime) => {
    for (const [key, record] of attempts) {
      if (currentTime - record.lastAt > ATTEMPT_TTL_MS) attempts.delete(key);
    }
  };

  return {
    /** Milliseconds the caller must wait, or 0 if it may attempt now. */
    retryAfterMs(key) {
      const record = attempts.get(key);
      if (!record) return 0;
      return Math.max(0, record.lockedUntil - now());
    },
    recordFailure(key) {
      const currentTime = now();
      prune(currentTime);
      const record = attempts.get(key) || { fails: 0, lockedUntil: 0, lastAt: currentTime };
      record.fails += 1;
      record.lastAt = currentTime;
      if (record.fails >= MAX_FAILS_BEFORE_LOCKOUT) {
        const overage = record.fails - MAX_FAILS_BEFORE_LOCKOUT;
        const delay = Math.min(LOCKOUT_BASE_MS * 2 ** overage, LOCKOUT_CAP_MS);
        record.lockedUntil = currentTime + delay;
      }
      attempts.set(key, record);
      return record;
    },
    reset(key) {
      attempts.delete(key);
    },
    get size() {
      return attempts.size;
    },
  };
}

export function parseCookies(header) {
  const out = {};
  if (typeof header !== "string" || !header) return out;
  for (const piece of header.split(";")) {
    const eq = piece.indexOf("=");
    if (eq < 1) continue;
    const name = piece.slice(0, eq).trim();
    if (!name || name in out) continue;
    try {
      out[name] = decodeURIComponent(piece.slice(eq + 1).trim());
    } catch {
      // A malformed Cookie header is untrusted request data. Ignore that cookie
      // instead of allowing decodeURIComponent to reject the async HTTP handler.
    }
  }
  return out;
}

/**
 * Build the session cookie.
 *
 * `Secure` is on by default because the only reason to publish this server is to reach
 * it over HTTPS; it is overridable so a plain-HTTP localhost session still works for
 * development. `SameSite=Lax` lets a bookmark or typed URL carry the cookie while
 * keeping it off cross-site POSTs.
 */
export function sessionCookie(token, { secure = true, maxAgeSeconds = SESSION_TTL_MS / 1000 } = {}) {
  const parts = [
    `${SESSION_COOKIE}=${token}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    `Max-Age=${Math.floor(maxAgeSeconds)}`,
  ];
  if (secure) parts.push("Secure");
  return parts.join("; ");
}

export function clearedSessionCookie({ secure = true } = {}) {
  const parts = [`${SESSION_COOKIE}=`, "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"];
  if (secure) parts.push("Secure");
  return parts.join("; ");
}

/**
 * Assemble the gate.
 *
 * `enabled: false` is the explicit, must-be-asked-for escape hatch described at the top
 * of this file. `configured` is false when auth is on but no hash was supplied — the
 * caller is expected to refuse to start in that case rather than serve anything.
 */
export function createAuthGate({
  passwordHash = process.env.CIPHER_APP_PASSWORD_HASH || "",
  sessionSecretOverride = process.env.CIPHER_APP_SESSION_SECRET || "",
  enabled = String(process.env.CIPHER_APP_AUTH || "").toLowerCase() !== "off",
  secureCookies = String(process.env.CIPHER_APP_INSECURE_COOKIES || "") !== "1",
  now = () => Date.now(),
} = {}) {
  const hash = String(passwordHash || "").trim();
  const secret = hash ? sessionSecretFor(hash, sessionSecretOverride) : null;
  const limiter = createAttemptLimiter({ now });

  return {
    enabled,
    configured: Boolean(hash),
    secureCookies,

    /** True when this request carries a valid session (or auth is switched off). */
    isAuthenticated(req) {
      if (!enabled) return true;
      if (!secret) return false;
      const token = parseCookies(req.headers?.cookie)[SESSION_COOKIE];
      return verifySession(token, secret, { now: now() }) != null;
    },

    /**
     * Check a submitted password.
     * Returns { ok, cookie } on success, or { ok: false, retryAfterMs } on refusal.
     * A locked-out client is refused WITHOUT running scrypt, so the lockout also caps
     * the CPU an attacker can spend on our behalf.
     */
    async login(password, clientKey = "unknown") {
      if (!enabled) return { ok: true, cookie: null };
      const retryAfterMs = limiter.retryAfterMs(clientKey);
      if (retryAfterMs > 0) return { ok: false, retryAfterMs };
      if (!secret || !(await verifyPassword(String(password ?? ""), hash))) {
        const record = limiter.recordFailure(clientKey);
        return { ok: false, retryAfterMs: Math.max(0, record.lockedUntil - now()) };
      }
      limiter.reset(clientKey);
      return {
        ok: true,
        cookie: sessionCookie(signSession(secret, { now: now() }), { secure: secureCookies }),
      };
    },

    logoutCookie() {
      return clearedSessionCookie({ secure: secureCookies });
    },
  };
}
