const MAX_BODY_BYTES = 16 * 1024;

function boundedCredentialPayload(input) {
  const value = {
    action: String(input?.action || "connect"),
    key: String(input?.key || ""),
    secret: String(input?.secret || ""),
    options_feed: String(input?.optionsFeed || "opra"),
    stock_feed: String(input?.stockFeed || "sip"),
  };
  if (!value.key || !value.secret || value.key.length > 512 || value.secret.length > 512) {
    throw new Error("provider credentials are invalid");
  }
  if (!new Set(["opra", "indicative"]).has(value.options_feed)) throw new Error("options feed is invalid");
  if (!new Set(["sip", "iex"]).has(value.stock_feed)) throw new Error("stock feed is invalid");
  return value;
}

export function createProviderSessionClient({ coreUrl, internalToken, fetchImpl = globalThis.fetch } = {}) {
  const sessions = new Map();
  const baseUrl = String(coreUrl || "").replace(/\/+$/, "");
  const token = String(internalToken || "");

  async function request(userContext, body) {
    if (!baseUrl || !token || !userContext?.userId || !userContext?.accessToken) {
      throw new Error("provider session is unavailable");
    }
    const encoded = JSON.stringify(body);
    if (Buffer.byteLength(encoded, "utf8") > MAX_BODY_BYTES) throw new Error("provider session request is too large");
    const response = await fetchImpl(`${baseUrl}/internal/provider-session`, {
      method: "POST",
      headers: {
        accept: "application/json",
        "content-type": "application/json",
        "x-cipher-internal-token": token,
        "x-cipher-user-id": userContext.userId,
        "x-cipher-access-token": userContext.accessToken,
      },
      body: encoded,
    });
    let payload = {};
    try { payload = await response.json(); } catch { /* use generic error below */ }
    if (!response.ok) throw new Error(String(payload.error || "provider session unavailable").slice(0, 240));
    return payload;
  }

  return {
    async connect(input) {
      const userContext = {
        userId: String(input?.userId || ""),
        accessToken: String(input?.accessToken || ""),
      };
      const payload = boundedCredentialPayload({ ...input, action: "connect" });
      const result = await request(userContext, payload);
      const sessionId = String(result.provider_session_id || "");
      if (!sessionId) throw new Error("provider session did not return an ID");
      sessions.set(userContext.userId, sessionId);
      return { provider_session_id: sessionId, status: String(result.status || "connected") };
    },

    async status(userContext) {
      const sessionId = sessions.get(userContext.userId) || null;
      const result = await request(userContext, { action: "status", provider_session_id: sessionId });
      return {
        status: String(result.status || "disconnected"),
        options_feed: result.options_feed ?? null,
        stock_feed: result.stock_feed ?? null,
        expires_at: result.expires_at ?? null,
        read_only: true,
      };
    },

    async disconnect(userContext) {
      const sessionId = sessions.get(userContext.userId) || null;
      try {
        await request(userContext, { action: "disconnect", provider_session_id: sessionId });
      } finally {
        sessions.delete(userContext.userId);
      }
    },

    remember(userId, sessionId) {
      if (userId && sessionId) sessions.set(String(userId), String(sessionId));
    },

    clear(userId) {
      sessions.delete(String(userId || ""));
    },

    sessionFor(userId) {
      return sessions.get(String(userId || "")) || null;
    },
  };
}
