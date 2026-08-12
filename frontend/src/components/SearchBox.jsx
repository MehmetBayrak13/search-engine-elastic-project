import { useEffect, useRef, useState } from 'react';

/**
 * Streamlit'teki `components/search_input/frontend/index.html` custom
 * component'inin React karşılığı — aynı davranış (debounce'lu typing,
 * klavye navigasyonu, blur'da kapanma, mousedown ile blur'u engelleme),
 * ama artık ayrı bir iframe/postMessage protokolüne ihtiyaç yok; React
 * controlled input + normal state yeterli.
 *
 * `revision` parent'ın input metnini DIŞARIDAN zorla değiştirmek istediği
 * anları işaretler (öneri seçimi, örnek sorgu chip'i) — kullanıcı o an
 * yazıyorsa üzerine yazılmaz, yalnızca `revision` arttığında senkronize olur.
 */
export default function SearchBox({
  value,
  revision,
  suggestions,
  placeholder,
  debounceMs,
  panelMaxHeightPx,
  rowHeightPx,
  showImages,
  onTypingChange,
  onSubmit,
  onSelect,
}) {
  const [text, setText] = useState(value || '');
  const [activeIndex, setActiveIndex] = useState(-1);
  const [userClosed, setUserClosed] = useState(false);
  const [focused, setFocused] = useState(false);
  const debounceRef = useRef(null);
  const blurTimerRef = useRef(null);

  useEffect(() => {
    setText(value || '');
  }, [revision, value]);

  useEffect(
    () => () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
      if (blurTimerRef.current) window.clearTimeout(blurTimerRef.current);
    },
    [],
  );

  const scheduleTyping = (nextText) => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => onTypingChange(nextText), debounceMs);
  };

  const handleChange = (event) => {
    const nextText = event.target.value;
    setText(nextText);
    setUserClosed(false);
    setActiveIndex(-1);
    scheduleTyping(nextText);
  };

  const closePanel = () => {
    setUserClosed(true);
    setActiveIndex(-1);
  };

  const submitPlain = () => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    closePanel();
    onSubmit(text);
  };

  const selectAt = (idx) => {
    const item = suggestions[idx];
    if (!item) return;
    closePanel();
    setText(item.title);
    onSelect(item);
  };

  const panelVisible = focused && !userClosed && suggestions.length > 0;

  const handleKeyDown = (event) => {
    if (event.key === 'ArrowDown') {
      if (!panelVisible) return;
      event.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, suggestions.length - 1));
    } else if (event.key === 'ArrowUp') {
      if (!panelVisible) return;
      event.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, -1));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (panelVisible && activeIndex >= 0) {
        selectAt(activeIndex);
      } else {
        submitPlain();
      }
    } else if (event.key === 'Escape') {
      if (panelVisible) {
        event.preventDefault();
        closePanel();
      }
    }
  };

  const handleBlur = () => {
    blurTimerRef.current = window.setTimeout(() => {
      setFocused(false);
      closePanel();
    }, 120);
  };

  const formatMeta = (item) =>
    [
      item.store ? `🏬 ${item.store}` : null,
      item.average_rating != null ? `⭐ ${item.average_rating}` : null,
      item.price ? `💲 ${Number(item.price).toFixed(2)}` : null,
    ]
      .filter(Boolean)
      .join(' · ') || 'Detay yok';

  return (
    <div className="si-wrap">
      <span className="si-icon" aria-hidden="true">
        🔍
      </span>
      <input
        className="si-input"
        type="text"
        autoComplete="off"
        spellCheck="false"
        role="combobox"
        aria-expanded={panelVisible}
        aria-autocomplete="list"
        aria-controls="si-panel"
        placeholder={placeholder}
        value={text}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onFocus={() => setFocused(true)}
        onBlur={handleBlur}
      />
      {panelVisible && (
        <div className="si-panel" id="si-panel" role="listbox" style={{ maxHeight: panelMaxHeightPx }}>
          {suggestions.map((item, idx) => (
            <div
              key={item.asin || `${item.title}-${idx}`}
              id={`si-row-${idx}`}
              role="option"
              aria-selected={idx === activeIndex}
              className={`si-row${idx === activeIndex ? ' si-active' : ''}`}
              style={{ minHeight: rowHeightPx }}
              onMouseDown={(event) => event.preventDefault()}
              onMouseEnter={() => setActiveIndex(idx)}
              onClick={() => selectAt(idx)}
            >
              {showImages ? (
                <img
                  className="si-row-icon"
                  alt=""
                  src={item.image_url || ''}
                  onError={(event) => {
                    event.currentTarget.onerror = null;
                    event.currentTarget.classList.add('si-icon-broken');
                  }}
                />
              ) : (
                <div className="si-row-icon si-icon-fallback">🔍</div>
              )}
              <div className="si-row-info">
                <div className="si-row-title">{item.title}</div>
                <div className="si-row-meta">{formatMeta(item)}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
