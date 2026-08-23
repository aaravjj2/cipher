import { randomBytes } from "node:crypto";

export const AUTH_COOKIE_NAME = "cipher_session";
const DEFAULT_INACTIVITY_MS = 30 * 60 * 1000;
const DEFAULT_ABSOLUTE_MS = 12 * 60 * 60 * 1000;
const DEFAULT_GUEST_ABSOLUTE_MS = 2 * 60 * 60 * 1000;

function parseCookies(header) {
  const values = {};
  for (const part of String(header || "").split(";")) {
    const index = part.indexOf("=");
    if (index <= 0) continue;
    const key = part.slice(0, index).trim();
    const value = part.slice(index + 1).trim();
    if (key) values[key] = value;
  }
  return values;
}

function cookieValue(request) {
  return parseCookies(request?.headers?.cookie)?.[AUTH_COOKIE_NAME] || null;
}

function positive(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

function serialize(value, maxAge) {
  return `${AUTH_COOKIE_NAME}=${value}; Max-Age=${Math.max(0, Math.floor(maxAge))}; Path=/; HttpOnly; Secure; SameSite=None`;
}

export function createAuthSessionStore({
  now = () => Date.now(),
  randomId = () => randomBytes(32).toString("base64url"),
  inactivityMs = DEFAULT_INACTIVITY_MS,
  absoluteMs = DEFAULT_ABSOLUTE_MS,
  guestAbsoluteMs = DEFAULT_GUEST_ABSOLUTE_MS,
} = {}) {
  const sessions = new Map();
  const inactivity = positive(inactivityMs, DEFAULT_INACTIVITY_MS);
  const absolute = positive(absoluteMs, DEFAULT_ABSOLUTE_MS);
  const guestAbsolute = positive(guestAbsoluteMs, DEFAULT_GUEST_ABSOLUTE_MS);

  function create({ userId, accessToken, email = null, profile = null, guest = false }) {
    const id = String(randomId());
    const createdAt = now();
    sessions.set(id, {
      userId: String(userId),
      accessToken: accessToken ? String(accessToken) : null,
      email: email ? String(email) : null,
      profile,
      guest: Boolean(guest),
      createdAt,
      lastSeenAt: createdAt,
    });
    const sessionAbsolute = guest ? guestAbsolute : absolute;
    return serialize(id, Math.ceil(Math.min(inactivity, sessionAbsolute) / 1000));
  }

  function get(request) {
    const id = cookieValue(request);
    if (!id) return null;
    const session = sessions.get(id);
    if (!session) return null;
    const current = now();
    const sessionAbsolute = session.guest ? guestAbsolute : absolute;
    if (current - session.createdAt >= sessionAbsolute || current - session.lastSeenAt >= inactivity) {
      sessions.delete(id);
      return null;
    }
    session.lastSeenAt = current;
    return {
      userId: session.userId,
      accessToken: session.accessToken,
      email: session.email,
      profile: session.profile,
      guest: session.guest,
    };
  }

  function createGuest(profile) {
    return create({ userId: "guest", accessToken: null, profile, guest: true });
  }

  function clear(request) {
    const id = cookieValue(request);
    if (id) sessions.delete(id);
    return serialize("", 0);
  }

  return { create, createGuest, get, clear, size: () => sessions.size };
}
