"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let browserClient: SupabaseClient | null = null;

export function hostedApiUrl(path: string): string {
  const origin = process.env.NEXT_PUBLIC_CIPHER_API_ORIGIN?.trim().replace(/\/+$/, "");
  return origin ? `${origin}${path}` : path;
}

export function isSupabaseConfigured(): boolean {
  return Boolean(
    process.env.NEXT_PUBLIC_SUPABASE_URL
    && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  );
}

export function createBrowserSupabaseClient(): SupabaseClient {
  if (browserClient) return browserClient;
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) {
    throw new Error("Supabase Auth is not configured for this deployment.");
  }
  browserClient = createClient(url, anonKey, {
    auth: {
      // Hosted auth is exchanged for an HttpOnly cookie; never persist Supabase
      // access/refresh tokens in localStorage or sessionStorage.
      persistSession: false,
      autoRefreshToken: false,
      detectSessionInUrl: true,
    },
  });
  return browserClient;
}
