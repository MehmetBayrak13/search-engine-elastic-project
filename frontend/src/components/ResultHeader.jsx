import { formatMessage } from '../lib/format';

const SORT_OPTIONS = [
  { value: 'relevance', label: 'En alakalı' },
  { value: 'price-asc', label: 'Fiyat: düşükten yükseğe' },
  { value: 'price-desc', label: 'Fiyat: yüksekten düşüğe' },
  { value: 'rating', label: 'En yüksek puan' },
];

function titleCase(value) {
  return value.replace(/\w\S*/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase());
}

/**
 * `intent_debug.positive_categories`den kısa bir "sorgunu şöyle anladım"
 * özeti türetir. Ham listeyi olduğu gibi göstermiyoruz -- dinamik keşif
 * `significant_terms`in doğası gereği ilgisiz küçük mağazaları da
 * (ör. "smartwatch" için "blueshaw") "aday" olarak üretebiliyor (bkz.
 * services/search_service.py: discover_category_intent). Marka adayını
 * yalnızca sorgu METNİNDE gerçekten geçiyorsa gösteririz (ör. "nike
 * sneakers" -> "nike" sorguda var, göster; "smartwatch" -> "blueshaw"
 * sorguda yok, gösterme). Kategori adayı için bu filtre gerekmez --
 * kategori keşfinin amacı zaten sorguda hiç geçmeyen bir kelimeyi
 * (ör. "sneakers" -> "Shoes") çıkarmaktır.
 */
function deriveIntentSummary(intentDebug, queryText) {
  const categories = intentDebug?.positive_categories;
  if (!categories?.length) return null;

  const queryLower = (queryText || '').toLocaleLowerCase('tr');
  const brandEntry = categories.find(
    (c) => c.field === 'store' && queryLower.includes(String(c.value).toLocaleLowerCase('tr')),
  );
  const categoryEntry = categories.find((c) => c.field !== 'store');

  const brand = brandEntry ? titleCase(String(brandEntry.value)) : null;
  const category = categoryEntry ? titleCase(String(categoryEntry.value)) : null;
  if (!brand && !category) return null;
  return { brand, category };
}

/**
 * Sonuç durumu ne olursa olsun HER ZAMAN render olur — böylece "ayarları
 * gizle/göster" kontrolü, ilk yüklemede/boş durumda/sıfır sonuçta da
 * erişilebilir kalır (bkz. App.jsx: eskiden bu buton sidebar'ın yanında
 * ayrı bir sütundaydı, artık results-bar'a taşındı).
 */
export default function ResultHeader({
  config,
  query,
  total,
  intent,
  intentDebug,
  hasResults,
  sortMode,
  onSortChange,
  sidebarOpen,
  onToggleSidebar,
}) {
  const { messages } = config;
  const intentSummary = hasResults ? deriveIntentSummary(intentDebug, query) : null;

  return (
    <>
      <div className="results-bar">
        <div className="results-info">
          {hasResults ? (
            <>
              {formatMessage(messages.result_header_query, { query, total })}
              {intent && (
                <span className="intent-badge">
                  {intent.icon} {intent.label}
                </span>
              )}
            </>
          ) : (
            'Aramaya başlamak için bir ürün adı yaz'
          )}
        </div>
        <div className="results-tools">
          {hasResults && (
            <select
              className="sort-select"
              aria-label="Sıralama"
              value={sortMode}
              onChange={(event) => onSortChange(event.target.value)}
            >
              {SORT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            className="sidebar-toggle"
            onClick={onToggleSidebar}
            aria-expanded={sidebarOpen}
          >
            {sidebarOpen ? '‹ Gelişmiş ayarları gizle' : '⚙ Gelişmiş ayarlar'}
          </button>
        </div>
      </div>

      {intentSummary && (
        <p className="intent-summary">
          🔎 Sorgunu şöyle anladım:{' '}
          {intentSummary.brand && (
            <span className="intent-summary-chip">
              {intentSummary.brand} <i>marka</i>
            </span>
          )}
          {intentSummary.category && (
            <span className="intent-summary-chip">
              {intentSummary.category} <i>kategori</i>
            </span>
          )}
        </p>
      )}
    </>
  );
}
