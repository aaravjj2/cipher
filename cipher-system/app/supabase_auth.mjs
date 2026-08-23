import { createHash } from "node:crypto";

const DEFAULT_CACHE_TTL_MS = 30_000;
const MAX_TOKEN_LENGTH = 4096;

function tokenFromRequest(request) {
  const header = String(request?.headers?.authorization || "");
  const match = /^Bearer\s+([^\s]+)$/i.exec(header);
  if (!match || match[1].length > MAX_TOKEN_LENGTH) return null;
  return match[1];
}

function tokenCacheKey(token) {
  return createHash("sha256").update(token).digest("hex");
}

function normalizeUserId(value) {
  const userId = String(value || "").trim();
  return userId && userId.length <= 128 ? userId : null;
}

function normalizeEmail(value) {
  const email = String(value || "").trim().toLowerCase();
  return email && email.length <= 320 ? email : null;
}

function sanitizeAppMetadata(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const role = String(value.cipher_role || "").trim().toLowerCase();
  return role === "developer" ? { cipher_role: "developer" } : {};
}

export function createSupabaseAuth({
  supabaseUrl,
  anonKey,
  fetchImpl = globalThis.fetch,
  cacheTtlMs = DEFAULT_CACHE_TTL_MS,
} = {}) {
  const baseUrl = String(supabaseUrl || "").replace(/\/+$/, "");
  const publicKey = String(anonKey || "");
  const cache = new Map();

  async function validateAccessToken(accessToken) {
    if (!accessToken || !baseUrl || !publicKey || typeof fetchImpl !== "function") return null;

    const key = tokenCacheKey(accessToken);
    const cached = cache.get(key);
    if (cached && cached.expiresAt > Date.now()) {
      return { userId: cached.userId, email: cached.email, appMetadata: cached.appMetadata, databaseAccess: cached.databaseAccess, accessToken };
    }
    if (cached) cache.delete(key);

    try {
      const response = await fetchImpl(`${baseUrl}/auth/v1/user`, {
        method: "GET",
        headers: {
          accept: "application/json",
          apikey: publicKey,
          authorization: `Bearer ${accessToken}`,
        },
      });
      if (!response.ok) return null;
      const payload = await response.json();
      const userId = normalizeUserId(payload?.id || payload?.user?.id);
      if (!userId) return null;
      const email = normalizeEmail(payload?.email || payload?.user?.email);
      const appMetadata = sanitizeAppMetadata(payload?.app_metadata || payload?.user?.app_metadata);
      let databaseAccess = null;
      try {
        const accessResponse = await fetchImpl(
          `${baseUrl}/rest/v1/account_access?select=role,developer_settings&user_id=eq.${encodeURIComponent(userId)}&limit=1`,
          { headers: { accept: "application/json", apikey: publicKey, authorization: `Bearer ${accessToken}` } },
        );
        if (accessResponse.ok) databaseAccess = (await accessResponse.json())?.[0] || null;
      } catch {
        // The access table is additive. Existing deployments remain usable until its
        // migration is applied; app_metadata/operator allowlists still work meanwhile.
      }
      cache.set(key, { userId, email, appMetadata, databaseAccess, expiresAt: Date.now() + Math.max(0, Number(cacheTtlMs) || 0) });
      return { userId, email, appMetadata, databaseAccess, accessToken };
    } catch {
      // Authentication failures are intentionally indistinguishable to callers.
      // Do not include the token, provider response, or exception in an API error.
      return null;
    }
  }

  async function validateRequest(request) {
    return validateAccessToken(tokenFromRequest(request));
  }

  function invalidate(accessToken) {
    if (typeof accessToken === "string" && accessToken) cache.delete(tokenCacheKey(accessToken));
  }

  return { validateRequest, validateAccessToken, invalidate };
}
