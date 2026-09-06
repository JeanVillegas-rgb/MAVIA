import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { resendVerification, verifyEmail } from "../api";

function Shell({ eyebrow, title, children }) {
  return (
    <div className="mv-root">
      <div className="mv-auth">
        <aside className="mv-auth__aside">
          <Link to="/" className="mv-brand">
            <span className="mv-brand__dot" aria-hidden="true" />
            MAVIA
          </Link>
          <div>
            <h2>Shaped for Touch,</h2>
            <p>Heard to Learn.</p>
          </div>
          <p className="mv-muted" style={{ color: "rgba(255,255,255,.6)" }}>
            Email verification
          </p>
        </aside>
        <div className="mv-auth__main">
          <div className="mv-auth__card">
            {eyebrow ? <p className="mv-eyebrow">{eyebrow}</p> : null}
            <h1>{title}</h1>
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function VerifyEmailPage() {
  const { token } = useParams();
  const isResendMode = !token || token === "resend";

  // "confirm" (button not yet pressed) -> "loading" -> "success" | "error".
  // Verification is a deliberate click, not an effect fired on page load:
  // an automatic call would double-fire under React StrictMode in dev, and
  // in production it's exactly the kind of state-changing GET that mail
  // clients (Gmail/Outlook link-scanning) can silently prefetch and burn
  // before the user ever clicks it.
  const [status, setStatus] = useState(isResendMode ? "resend" : "confirm");
  const [message, setMessage] = useState("");

  const [email, setEmail] = useState("");
  const [resending, setResending] = useState(false);
  const [resent, setResent] = useState(false);
  const [resendError, setResendError] = useState("");

  async function handleVerify() {
    setStatus("loading");
    try {
      const res = await verifyEmail(token);
      setStatus("success");
      setMessage(res.message || "Email verified. You can now log in.");
    } catch (err) {
      setStatus("error");
      setMessage(err.message || "Verification failed or the link has expired.");
    }
  }

  async function handleResend(event) {
    event.preventDefault();
    if (!/\S+@\S+\.\S+/.test(email)) {
      setResendError("Enter a valid email address.");
      return;
    }
    setResendError("");
    setResending(true);
    try {
      const res = await resendVerification(email);
      setResent(true);
      setMessage(res.message || "If that account exists and is unverified, a new link is on its way.");
    } catch (err) {
      setResendError(err.message || "Could not resend. Try again.");
    } finally {
      setResending(false);
    }
  }

  if (status === "confirm") {
    return (
      <Shell eyebrow="Almost there" title="Verify your email">
        <p>Click below to confirm this is you and activate your account.</p>
        <button
          className="mv-btn mv-btn--block mv-btn--lg"
          type="button"
          style={{ marginTop: "1.5rem" }}
          onClick={handleVerify}
        >
          Confirm my email
        </button>
      </Shell>
    );
  }

  if (status === "loading") {
    return (
      <Shell eyebrow="One moment" title="Verifying your email…">
        <p>Confirming your link with the server.</p>
      </Shell>
    );
  }

  if (status === "success") {
    return (
      <Shell eyebrow="All done" title="Email verified">
        <p>{message}</p>
        <p className="mv-auth__foot" style={{ marginTop: "2rem" }}>
          <Link to="/login">Go to log in</Link>
        </p>
      </Shell>
    );
  }

  // error or resend mode -> show the resend form
  return (
    <Shell
      eyebrow={status === "error" ? "Link problem" : "Resend"}
      title={status === "error" ? "That link didn't work" : "Resend verification email"}
    >
      {status === "error" && <p>{message}</p>}

      {resent ? (
        <p style={{ marginTop: "1rem" }}>{message}</p>
      ) : (
        <form className="mv-form" onSubmit={handleResend}>
          <div className="mv-field">
            <label htmlFor="resend-email">Your email</label>
            <input
              id="resend-email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
                setResendError("");
              }}
            />
          </div>
          {resendError && <div className="mv-alert">{resendError}</div>}
          <button
            className="mv-btn mv-btn--block mv-btn--lg"
            type="submit"
            disabled={resending}
          >
            {resending ? "Sending…" : "Resend verification email"}
          </button>
        </form>
      )}

      <p className="mv-auth__foot" style={{ marginTop: "1.5rem" }}>
        <Link to="/login">Back to log in</Link>
      </p>
    </Shell>
  );
}
