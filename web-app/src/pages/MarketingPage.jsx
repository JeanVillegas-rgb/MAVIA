import PublicNav from "../components/PublicNav";

// Lightweight stand-in for the /about and /contact marketing routes so the
// public nav links resolve. Content is placeholder copy.
export default function MarketingPage({ title, children }) {
  return (
    <div className="mv-root">
      <PublicNav />
      <div className="mv-landing">
        <section className="mv-hero" style={{ background: "var(--mv-brand-50)" }}>
          <p className="mv-eyebrow">MAVIA</p>
          <h1 style={{ marginTop: ".5rem" }}>{title}</h1>
          <p className="mv-hero__lede">{children}</p>
        </section>
      </div>
    </div>
  );
}
