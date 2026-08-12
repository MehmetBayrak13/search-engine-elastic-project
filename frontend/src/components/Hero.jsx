export default function Hero({ hero, children }) {
  if (!hero) return null;
  return (
    <section className="hero">
      <h1 className="hero-title">{hero.title}</h1>
      <p className="hero-sub">{hero.subtitle}</p>
      <div className="search-deck">{children}</div>
    </section>
  );
}
