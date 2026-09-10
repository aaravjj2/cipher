"use client";

import { useEffect, useState } from "react";
import { createBrowserSupabaseClient, hostedApiUrl, isSupabaseConfigured } from "@/lib/supabase";
import { resetRequestCache } from "@/lib/requestCache";

export type AccessCapabilities = {
  marketData: boolean;
  research: boolean;
  savedWorkspace: boolean;
  providerConnection: boolean;
  developerTools: boolean;
  liveOrders: boolean;
};

export type AuthIdentity = {
  mode: "developer" | "member" | "guest";
  role: "developer" | "member" | "guest";
  user: { id: string; email?: string | null } | null;
  capabilities: AccessCapabilities;
  settings: { displayName?: string; defaultTicker?: string; defaultPanel?: string };
};

export type AuthState = {
  configured: boolean;
  loading: boolean;
  session: AuthIdentity | null;
  error: string | null;
  providerAvailable: boolean | null;
};

function readableAuthError(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "Authentication is temporarily unavailable.";
}

async function fetchCookieSession(): Promise<AuthIdentity | null> {
  const response = await fetch(hostedApiUrl("/auth/session"), {
    cache: "no-store",
    credentials: "include",
  });
  if (!response.ok) return null;
  const payload = await response.json() as Partial<AuthIdentity> & { authenticated?: boolean };
  const userId = String(payload.user?.id || "").trim();
  if (payload.mode === "guest") return payload as AuthIdentity;
  return payload.authenticated && userId ? payload as AuthIdentity : null;
}

async function fetchAuthProviderAvailability(): Promise<boolean | null> {
  try {
    const response = await fetch(hostedApiUrl("/auth/status"), {
      cache: "no-store",
      credentials: "include",
    });
    if (!response.ok) return null;
    const payload = await response.json() as { configured?: boolean; reachable?: boolean };
    return payload.configured === true && payload.reachable === true;
  } catch {
    return null;
  }
}

export function useAuthSession(): AuthState {
  const configured = isSupabaseConfigured();
  const [state, setState] = useState<AuthState>({
    configured,
    loading: configured,
    session: null,
    error: null,
    providerAvailable: null,
  });

  useEffect(() => {
    if (!configured) return undefined;

    let active = true;
    const load = async () => {
      try {
        const [session, providerAvailable] = await Promise.all([
          fetchCookieSession(),
          fetchAuthProviderAvailability(),
        ]);
        if (active) setState({ configured: true, loading: false, session, error: null, providerAvailable });
      } catch (error) {
        if (active) setState({ configured: true, loading: false, session: null, error: readableAuthError(error), providerAvailable: null });
      }
    };
    void load();
    window.addEventListener("cipher-auth-changed", load);
    return () => {
      active = false;
      window.removeEventListener("cipher-auth-changed", load);
    };
  }, [configured]);

  return state;
}

export async function establishCookieSession(accessToken: string): Promise<AuthIdentity> {
  const response = await fetch(hostedApiUrl("/auth/session"), {
    method: "POST",
    cache: "no-store",
    credentials: "include",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new Error("Unable to establish the secure browser session.");
  const payload = await response.json() as AuthIdentity & { authenticated?: boolean };
  const userId = String(payload.user?.id || "").trim();
  if (!payload.authenticated || !userId) throw new Error("The secure browser session was not created.");
  const identity = payload;
  window.dispatchEvent(new Event("cipher-auth-changed"));
  return identity;
}

export async function establishGuestSession(): Promise<AuthIdentity> {
  const response = await fetch(hostedApiUrl("/auth/guest"), {
    method: "POST",
    cache: "no-store",
    credentials: "include",
  });
  if (!response.ok) throw new Error("Guest access is temporarily unavailable.");
  const identity = await response.json() as AuthIdentity;
  if (identity.mode !== "guest") throw new Error("The guest session was not created.");
  window.dispatchEvent(new Event("cipher-auth-changed"));
  return identity;
}

export async function establishOperatorSession(password: string): Promise<AuthIdentity> {
  const response = await fetch(hostedApiUrl("/auth/operator"), {
    method: "POST",
    cache: "no-store",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { error?: string };
    throw new Error(payload.error === "invalid credentials" ? "Invalid host password." : "Host access is temporarily unavailable.");
  }
  const identity = await response.json() as AuthIdentity;
  if (identity.mode !== "developer") throw new Error("The host session was not created.");
  window.dispatchEvent(new Event("cipher-auth-changed"));
  return identity;
}

export async function signOut(): Promise<void> {
  resetRequestCache();
  await fetch(hostedApiUrl("/auth/session"), {
    method: "DELETE",
    cache: "no-store",
    credentials: "include",
  }).catch(() => {});
  // The HttpOnly application cookie is the authority. Update the app immediately
  // after it is revoked; waiting for Supabase's in-memory cleanup previously left the
  // terminal visibly signed in even though DELETE /auth/session had succeeded.
  resetRequestCache();
  window.dispatchEvent(new Event("cipher-auth-changed"));
  // Supabase is configured with persistSession=false; this is best-effort cleanup of
  // the temporary in-memory login result used for the one-time cookie exchange.
  void createBrowserSupabaseClient().auth.signOut({ scope: "local" }).catch(() => {});
  // A hard navigation also clears panel-local caches and subscriptions. More
  // importantly, it re-derives the screen from the server cookie instead of relying
  // on every mounted hook to observe the custom event during a teardown race.
  window.location.replace("/");
}

/** Hosted API authentication is cookie-based; no bearer token is persisted or attached. */
export async function getAccessToken(): Promise<string | null> {
  return null;
}
