export default function Hero({ hero }) {
  if (!hero) return null;
  return (
    <div className="hero">
      <div className="hero-logo">{hero.logo}</div>
      <h1 className="hero-title">{hero.title}</h1>
      <p className="hero-subtitle">{hero.subtitle}</p>
    </div>
  );
}
