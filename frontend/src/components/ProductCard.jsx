import { useState } from 'react';

function openProduct(url) {
  if (url) window.open(url, '_blank', 'noopener,noreferrer');
}

const FIELD_LABELS = {
  title: 'Başlık',
  'title.tr': 'Başlık',
  features: 'Özellikler',
  'features.tr': 'Özellikler',
  description: 'Açıklama',
  'description.tr': 'Açıklama',
  categories_text: 'Kategori',
  'categories_text.tr': 'Kategori',
};

function fieldLabel(field) {
  return FIELD_LABELS[field] || field;
}

/**
 * Başlıkta sorgu kelimelerini <mark> ile vurgular. `dangerouslySetInnerHTML`
 * KULLANILMAZ — React node dizisi (string + <mark>) döner, bu yüzden XSS
 * riski yoktur (bkz. CLAUDE.md §5: unsafe HTML üretilmemeli).
 */
function highlightTitle(title, query) {
  const words = (query || '')
    .toLocaleLowerCase('tr')
    .split(/\s+/)
    .filter((w) => w.length > 2)
    .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (words.length === 0) return title;

  const pattern = new RegExp(`(${words.join('|')})`, 'gi');
  const parts = title.split(pattern);
  return parts.map((part, index) =>
    pattern.test(part) ? (
      // eslint-disable-next-line react/no-array-index-key
      <mark key={index}>{part}</mark>
    ) : (
      part
    ),
  );
}

export default function ProductCard({ product, query, maxScore, explain }) {
  const [explainOpen, setExplainOpen] = useState(false);
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

  const priceValue = Number(price);
  const hasPrice = price != null && !Number.isNaN(priceValue) && priceValue > 0;
  const hasRating = averageRating != null;
  const matchPct = maxScore > 0 ? Math.min(100, Math.round((score / maxScore) * 100)) : 0;

  const handleActivate = () => openProduct(productUrl);

  return (
    <article
      className="card"
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
      <div className="card-media">
        <img
          src={imageUrl}
          alt=""
          onError={(event) => {
            event.currentTarget.onerror = null;
            event.currentTarget.style.opacity = '0.35';
          }}
        />
      </div>
      <div className="card-body">
        <span className="card-cat">{mainCategory || 'Kategori yok'}</span>
        <h3 className="card-title">{highlightTitle(title, query)}</h3>

        {hasRating ? (
          <div className="rating">
            <span className="stars">
              <span className="stars-fill" style={{ width: `${(averageRating / 5) * 100}%` }} />
            </span>
            <span>{averageRating}</span>
            <span className="count">({(ratingNumber || 0).toLocaleString('tr-TR')})</span>
          </div>
        ) : (
          <div className="rating">
            <span className="count">Değerlendirme yok</span>
          </div>
        )}

        <div className="card-foot">
          {hasPrice ? (
            <div className="price">
              <span className="cur">$</span>
              {priceValue.toFixed(2)}
            </div>
          ) : (
            <div className="price" />
          )}
          {store && <span className="stock">{store}</span>}
        </div>

        <div className="card-meta">
          <span className="asin">{product.asin}</span>
          <span className="match">
            eşleşme
            <span className="match-bar">
              <i style={{ width: `${matchPct}%` }} />
            </span>
          </span>
        </div>

        {explain && (
          <div className="explain">
            <button
              type="button"
              className="explain-toggle"
              aria-expanded={explainOpen}
              onClick={(event) => {
                event.stopPropagation();
                setExplainOpen((open) => !open);
              }}
            >
              ⓘ Neden bu sonuç?
            </button>
            {explainOpen && (
              <div className="explain-panel" onClick={(event) => event.stopPropagation()}>
                {explain.matched_fields.length > 0 ? (
                  <div className="explain-row">
                    <b>Eşleşen alanlar:</b> {explain.matched_fields.map(fieldLabel).join(', ')}
                  </div>
                ) : explain.translation_matched ? (
                  <div className="explain-row">
                    🌐 <b>Çeviri ile eşleşti</b> — sorgunun İngilizce karşılığı bulundu
                  </div>
                ) : (
                  <div className="explain-row">
                    Bu ürün sorguyla eşleşti, ancak hangi alanda eşleştiği bu sorguda görüntülenemedi.
                  </div>
                )}
                <div className="explain-row">
                  <b>Konsensüs:</b> {explain.consensus_level} alan
                  {explain.consensus_level >= 2 ? ' — birden fazla alanda eşleşti, skor güçlendirildi' : ''}
                </div>
                {explain.contradictions.length > 0 && (
                  <div className="explain-row explain-warn">
                    ⚠ Çelişkili sinyal: {explain.contradictions.map(fieldLabel).join(', ')} — ceza uygulandı
                  </div>
                )}
                {explain.applied_penalty < 1 && (
                  <div className="explain-row explain-warn">
                    ⚠ Skor ×{explain.applied_penalty.toFixed(2)} ile düşürüldü
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
      <button
        type="button"
        className="card-cta"
        onClick={(event) => {
          event.stopPropagation();
          handleActivate();
        }}
      >
        Ürünü incele
      </button>
    </article>
  );
}
