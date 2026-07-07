import { FormEvent, useState } from "react";
import { LockKeyhole, Radar } from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import { formatError } from "../components/ui";

export function LoginScreen() {
  const { login, authMessage } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      await login(email, password);
    } catch (requestError) {
      setError(formatError(requestError));
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="login-screen">
      <section className="login-card" aria-labelledby="login-title">
        <div className="brand-mark">
          <Radar aria-hidden="true" />
          <span>RSAP</span>
        </div>
        <h1 id="login-title">Local Surveillance Console</h1>
        <p>Sign in through the desktop daemon to manage cameras, sync state, and runtime orchestration.</p>
        {authMessage ? <div className="info-banner">{authMessage}</div> : null}
        {error ? (
          <div role="alert" className="error-banner">
            {error}
          </div>
        ) : null}
        <form onSubmit={onSubmit} className="login-form">
          <label>
            Email
            <input
              autoComplete="username"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </label>
          <label>
            Password
            <input
              autoComplete="current-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          <button type="submit" disabled={pending}>
            <LockKeyhole aria-hidden="true" />
            {pending ? "Signing in" : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}
