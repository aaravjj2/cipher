"use client";

import { FormEvent, useState } from "react";
import { createBrowserSupabaseClient, isSupabaseConfigured } from "@/lib/supabase";
import { establishCookieSession, signOut, useAuthSession } from "@/lib/auth";

export function AuthPanel({ onGuestContinue }: { onGuestContinue: () => void }) {
  const auth = useAuthSession();
  const [mode, setMode] = useState<"sign-in" | "sign-up">("sign-in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (!isSupabaseConfigured()) return null;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setMessage(null);
    try {
      const client = createBrowserSupabaseClient();
      const result = mode === "sign-in"
        ? await client.auth.signInWithPassword({ email: email.trim(), password })
        : await client.auth.signUp({ email: email.trim(), password });
      if (result.error) throw result.error;
      const accessToken = result.data.session?.access_token;
      if (accessToken) {
        await establishCookieSession(accessToken);
        await client.auth.signOut({ scope: "local" });
        setMessage(mode === "sign-up" ? "Account created and signed in." : "Signed in securely.");
      } else {
        setMessage("Account created. Check your email, then sign in to continue.");
      }
      setPassword("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Authentication failed.");
    } finally {
      setSubmitting(false);
    }
  };

  if (auth.session) {
    return (
      <section className="flex min-h-screen items-center justify-center p-6" style={{ background: "var(--bg)", color: "var(--text)" }}>
        <div className="flex w-full max-w-md flex-col gap-5 rounded-xl p-7" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
          <div>
            <p className="text-[11px] font-bold uppercase tracking-[0.14em]" style={{ color: "var(--accent)" }}>Cipher</p>
            <h1 className="mt-2 text-2xl font-semibold">Session active</h1>
            <p className="mt-2 text-sm" style={{ color: "var(--text-dim)" }}>You are authenticated. Provider credentials are entered separately and remain session-only.</p>
          </div>
          <button type="button" onClick={() => void signOut()} className="rounded-md px-3 py-2 text-sm font-semibold" style={{ background: "var(--nav-active)", color: "var(--text)" }}>
            Sign out
          </button>
        </div>
      </section>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-6" style={{ background: "var(--bg)", color: "var(--text)" }}>
      <section data-testid="auth-panel" className="flex w-full max-w-md flex-col gap-6 rounded-xl p-7" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.14em]" style={{ color: "var(--accent)" }}>Cipher</p>
          <h1 className="mt-2 text-2xl font-semibold">Research terminal access</h1>
          <p className="mt-2 text-sm leading-relaxed" style={{ color: "var(--text-dim)" }}>
            Sign in to access your isolated research workspace. Cipher remains read-only and has no broker-order authority.
          </p>
        </div>

        <form className="flex flex-col gap-4" onSubmit={submit}>
          <label className="flex flex-col gap-1.5 text-sm">
            <span style={{ color: "var(--text-dim)" }}>Email</span>
            <input required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} className="rounded-md px-3 py-2" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }} />
          </label>
          <label className="flex flex-col gap-1.5 text-sm">
            <span style={{ color: "var(--text-dim)" }}>Password</span>
            <input required minLength={8} type="password" autoComplete={mode === "sign-in" ? "current-password" : "new-password"} value={password} onChange={(event) => setPassword(event.target.value)} className="rounded-md px-3 py-2" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }} />
          </label>
          <button type="submit" disabled={submitting} className="rounded-md px-3 py-2 text-sm font-semibold disabled:opacity-50" style={{ background: "var(--accent)", color: "var(--bg)" }}>
            {submitting ? "Working…" : mode === "sign-in" ? "Sign in" : "Create account"}
          </button>
        </form>

        <div className="rounded-lg border p-3" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }}>
          <p className="text-[12px] leading-relaxed" style={{ color: "var(--text-dim)" }}>
            Want to explore without an account? Guest mode uses delayed/unofficial Yahoo Finance data only. Saved workspace data and Alpaca connections require sign-in.
          </p>
          <button type="button" onClick={onGuestContinue} className="mt-3 rounded-md border px-3 py-2 text-sm font-semibold" style={{ borderColor: "var(--line)", color: "var(--text)" }}>
            Continue as guest
          </button>
        </div>

        {message && <p role="status" className="text-sm" style={{ color: "var(--text-dim)" }}>{message}</p>}
        {auth.error && <p role="alert" className="text-sm" style={{ color: "var(--neg)" }}>{auth.error}</p>}

        <button type="button" className="self-start text-sm underline underline-offset-4" style={{ color: "var(--text-dim)" }} onClick={() => { setMode((current) => current === "sign-in" ? "sign-up" : "sign-in"); setMessage(null); }}>
          {mode === "sign-in" ? "Need an account? Sign up" : "Already have an account? Sign in"}
        </button>
      </section>
    </main>
  );
}
