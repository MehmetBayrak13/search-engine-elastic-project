import { formatMessage } from '../lib/format';

const SORT_OPTIONS = [
  { value: 'relevance', label: 'En alakalı' },
  { value: 'price-asc', label: 'Fiyat: düşükten yükseğe' },
  { value: 'price-desc', label: 'Fiyat: yüksekten düşüğe' },
  { value: 'rating', label: 'En yüksek puan' },
];

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
  hasResults,
  sortMode,
  onSortChange,
  sidebarOpen,
  onToggleSidebar,
}) {
  const { messages } = config;

  return (
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
          {sidebarOpen ? '‹ Ayarları gizle' : '› Ayarları göster'}
        </button>
      </div>
    </div>
  );
}
