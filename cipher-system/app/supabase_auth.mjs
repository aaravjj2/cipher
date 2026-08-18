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
      return { userId: cached.userId, accessToken };
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
      cache.set(key, { userId, expiresAt: Date.now() + Math.max(0, Number(cacheTtlMs) || 0) });
      return { userId, accessToken };
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
