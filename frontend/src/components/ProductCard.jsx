function openProduct(url) {
  if (url) window.open(url, '_blank', 'noopener,noreferrer');
}

export default function ProductCard({ product }) {
  const {
    title,
    store,
    main_category: mainCategory,
    average_rating: averageRating,
    rating_number: ratingNumber,
    price,
    image_url: imageUrl,
    score,
    product_url: productUrl,
  } = product;

  const ratingText =
    averageRating != null ? `⭐ ${averageRating}  ·  ${ratingNumber || 0} değerlendirme` : 'Değerlendirme yok';

  const priceValue = Number(price);
  const hasPrice = price != null && !Number.isNaN(priceValue) && priceValue > 0;

  const handleActivate = () => openProduct(productUrl);

  return (
    <div
      className="product-card"
      role="link"
      tabIndex={0}
      aria-label={title}
      onClick={handleActivate}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          handleActivate();
        }
      }}
    >
      <img
        className="product-image"
        src={imageUrl}
        alt="Ürün görseli"
        onError={(event) => {
          event.currentTarget.onerror = null;
          event.currentTarget.style.opacity = '0.4';
        }}
      />
      <div className="product-info">
        <div className="product-title">{title}</div>
        <div className="product-meta">🏬 Mağaza: {store || 'Bilinmiyor'}</div>
        <div className="product-meta">📂 Kategori: {mainCategory || 'Kategori yok'}</div>
        <div className="product-rating">{ratingText}</div>
        {hasPrice && <div className="product-price">💲 {priceValue.toFixed(2)}</div>}
        <span className="score-badge">Skor: {Number(score).toFixed(2)}</span>
      </div>
    </div>
  );
}
