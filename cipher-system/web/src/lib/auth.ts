"use client";

import { useEffect, useState } from "react";
import { createBrowserSupabaseClient, hostedApiUrl, isSupabaseConfigured } from "@/lib/supabase";
import { resetRequestCache } from "@/lib/requestCache";

export type AuthIdentity = {
  user: { id: string; email?: string | null };
};

export type AuthState = {
  configured: boolean;
  loading: boolean;
  session: AuthIdentity | null;
  error: string | null;
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
  const payload = await response.json() as { authenticated?: boolean; user?: { id?: string; email?: string | null } };
  const userId = String(payload.user?.id || "").trim();
  return payload.authenticated && userId
    ? { user: { id: userId, email: payload.user?.email ?? null } }
    : null;
}

export function useAuthSession(): AuthState {
  const configured = isSupabaseConfigured();
  const [state, setState] = useState<AuthState>({
    configured,
    loading: configured,
    session: null,
    error: null,
  });

  useEffect(() => {
    if (!configured) return undefined;

    let active = true;
    const load = async () => {
      try {
        const session = await fetchCookieSession();
        if (active) setState({ configured: true, loading: false, session, error: null });
      } catch (error) {
        if (active) setState({ configured: true, loading: false, session: null, error: readableAuthError(error) });
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
  const payload = await response.json() as { authenticated?: boolean; user?: { id?: string; email?: string | null } };
  const userId = String(payload.user?.id || "").trim();
  if (!payload.authenticated || !userId) throw new Error("The secure browser session was not created.");
  const identity = { user: { id: userId, email: payload.user?.email ?? null } };
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
  // Supabase is configured with persistSession=false; this clears only the temporary
  // in-memory login result used for the one-time cookie exchange.
  await createBrowserSupabaseClient().auth.signOut({ scope: "local" }).catch(() => {});
  resetRequestCache();
  window.dispatchEvent(new Event("cipher-auth-changed"));
}

/** Hosted API authentication is cookie-based; no bearer token is persisted or attached. */
export async function getAccessToken(): Promise<string | null> {
  return null;
}
