"use client";

import { FormEvent, useState } from "react";
import { ApiError, api, type User } from "@/lib/api";
import { AlertIcon, ArrowIcon, LockIcon, ShieldIcon, SparkIcon } from "./icons";

export function AuthScreen({
  onAuthenticated,
}: {
  onAuthenticated: (token: string, user: User) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      const tokenResponse = await api.login(username, password);
      const user = await api.me(tokenResponse.access_token);
      onAuthenticated(tokenResponse.access_token, user);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Authentication failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-intro">
        <div className="auth-brand"><span>TF</span> Tron<strong>Forge</strong></div>
        <span className="section-kicker">Private by construction</span>
        <h1>Your TRON address.<br />Forged for you.</h1>
        <p>Start and monitor server-managed GPU wallet generation from one operator account.</p>
        <ul>
          <li><ShieldIcon /> Independently verified results</li>
          <li><LockIcon /> Split-key wallet generation</li>
          <li><SparkIcon /> All available workers on one job</li>
        </ul>
      </section>

      <section className="auth-card panel">
        <span className="section-kicker">Secure workspace</span>
        <h2>Operator sign in.</h2>
        <p>Enter the single username and password configured on the API server.</p>

        <form onSubmit={submit}>
          <label className="text-field">
            <span>Username</span>
            <div><input value={username} onChange={(event) => setUsername(event.target.value)} minLength={3} maxLength={64} autoComplete="username" required /></div>
          </label>
          <label className="text-field">
            <span>Password</span>
            <div><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={12} maxLength={128} autoComplete="current-password" required /></div>
          </label>
          {error && <p className="form-error" role="alert"><AlertIcon />{error}</p>}
          <button className="primary-button wide" disabled={submitting} type="submit">
            {submitting ? "Connecting…" : "Sign in"} {!submitting && <ArrowIcon />}
          </button>
        </form>
      </section>
    </main>
  );
}
