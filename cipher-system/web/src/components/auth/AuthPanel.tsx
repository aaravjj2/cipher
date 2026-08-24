"use client";

import { FormEvent, useState } from "react";
import { createBrowserSupabaseClient, isSupabaseConfigured } from "@/lib/supabase";
import { establishCookieSession, establishGuestSession } from "@/lib/auth";

export function AuthPanel({ authError = null }: { authError?: string | null }) {
  const [mode, setMode] = useState<"sign-in" | "sign-up">("sign-in");
  const [email, setEmail] = useState("");
  const [resetMode, setResetMode] = useState(false);
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [guestBusy, setGuestBusy] = useState(false);

  if (!isSupabaseConfigured()) return null;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setMessage(null);
    try {
      const client = createBrowserSupabaseClient();
      if (resetMode) {
        const result = await client.auth.resetPasswordForEmail(email.trim(), {
          redirectTo: window.location.origin,
        });
        if (result.error) throw result.error;
        setMessage("If that email is registered, Supabase sent password reset instructions. If nothing arrives, check spam or ask the Cipher operator to configure Supabase SMTP.");
        setResetMode(false);
        setPassword("");
        return;
      }
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
          <label htmlFor="cipher-auth-email" className="flex flex-col gap-1.5 text-sm">
            <span style={{ color: "var(--text-dim)" }}>Email</span>
            <input id="cipher-auth-email" name="email" required type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} className="rounded-[4px] px-3 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold)]" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }} />
          </label>
          {!resetMode && <label htmlFor="cipher-auth-password" className="flex flex-col gap-1.5 text-sm">
            <span style={{ color: "var(--text-dim)" }}>Password</span>
            <input id="cipher-auth-password" name="password" required minLength={8} type="password" autoComplete={mode === "sign-in" ? "current-password" : "new-password"} value={password} onChange={(event) => setPassword(event.target.value)} className="rounded-[4px] px-3 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold)]" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }} />
          </label>}
          <button type="submit" disabled={submitting} className="rounded-[4px] px-3 py-2 text-sm font-semibold disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold)]" style={{ background: "var(--accent)", color: "var(--bg)" }}>
            {submitting ? "Working…" : resetMode ? "Send reset email" : mode === "sign-in" ? "Sign in" : "Create account"}
          </button>
        </form>

        <div className="rounded-lg border p-3" style={{ borderColor: "var(--line)", background: "var(--panel-2)" }}>
          <p className="text-[12px] leading-relaxed" style={{ color: "var(--text-dim)" }}>
            Explore the complete read-only Cipher workflow without an account. Guest sessions include clearly labelled MAG7 showcase content plus bounded live charts, Night Vision, and Strike Matrix views. Private writes, provider connections, system controls, and every order capability stay locked.
          </p>
          <button type="button" disabled={guestBusy} onClick={() => { setGuestBusy(true); setMessage(null); void establishGuestSession().catch((error) => setMessage(error instanceof Error ? error.message : "Guest access failed.")).finally(() => setGuestBusy(false)); }} className="mt-3 rounded-[4px] border px-3 py-2 text-sm font-semibold disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold)]" style={{ borderColor: "var(--line)", color: "var(--text)" }}>
            {guestBusy ? "Opening demo…" : "Continue as guest"}
          </button>
        </div>

        {message && <p role="status" className="text-sm" style={{ color: "var(--text-dim)" }}>{message}</p>}
        {authError && <p role="alert" className="text-sm" style={{ color: "var(--neg)" }}>{authError}</p>}

        {mode === "sign-in" && <button type="button" className="self-start text-sm underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold)]" style={{ color: "var(--text-dim)" }} onClick={() => { setResetMode((current) => !current); setMessage(null); }}>
          {resetMode ? "Back to sign in" : "Forgot password?"}
        </button>}
        {!resetMode && <button type="button" className="self-start text-sm underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--gold)]" style={{ color: "var(--text-dim)" }} onClick={() => { setMode((current) => current === "sign-in" ? "sign-up" : "sign-in"); setMessage(null); }}>
          {mode === "sign-in" ? "Need an account? Sign up" : "Already have an account? Sign in"}
        </button>}
      </section>
    </main>
  );
}
