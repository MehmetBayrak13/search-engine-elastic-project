import { useEffect, useRef, useState } from 'react';
import { ApiError, getAutocomplete, getConfig, getHealth, searchProducts } from './api/client';
import Hero from './components/Hero';
import SidebarSettings from './components/SidebarSettings';
import SearchBox from './components/SearchBox';
import FeatureChips from './components/FeatureChips';
import EmptyState from './components/EmptyState';
import ResultHeader from './components/ResultHeader';
import ProductCard from './components/ProductCard';
import Pagination from './components/Pagination';
import { formatMessage } from './lib/format';
import './App.css';

const DEFAULT_FLAGS = {
  enablePhrase: true,
  enableMultiMatch: true,
  enableFuzzy: true,
  enableExactAsin: true,
};

function flagsSignature(flags) {
  return JSON.stringify(Object.entries(flags).sort());
}

export default function App() {
  const [config, setConfig] = useState(null);
  const [configError, setConfigError] = useState(null);

  const [flags, setFlags] = useState(DEFAULT_FLAGS);
  const [liveSuggestions, setLiveSuggestions] = useState(true);
  const [esOk, setEsOk] = useState(false);

  const [searchBoxValue, setSearchBoxValue] = useState('');
  const [searchBoxRevision, setSearchBoxRevision] = useState(0);
  const [queryText, setQueryText] = useState('');

  const [suggestions, setSuggestions] = useState([]);
  const [suggestionsError, setSuggestionsError] = useState(false);
  const [typedTooShort, setTypedTooShort] = useState(false);

  const [currentPage, setCurrentPage] = useState(1);
  const [result, setResult] = useState(null);
  const [resultFlagsSig, setResultFlagsSig] = useState(null);
  const [searchError, setSearchError] = useState(null);
  const [loading, setLoading] = useState(false);

  const resultsTopRef = useRef(null);

  useEffect(() => {
    getConfig()
      .then(setConfig)
      .catch((error) => setConfigError(error.message));
    // Health, config'ten ayrı bir sinyal olarak bilerek tutuluyor (ör.
    // ileride "ES bağlantısı var ama config dosyası da geçerli" ayrımı
    // gerekirse); şu an yalnızca sistem durumu rozetini etkiler.
    getHealth().catch(() => {});
  }, []);

  const executeSearch = async (text, page, activeFlags) => {
    const trimmed = (text || '').trim();
    if (!trimmed) {
      setSearchError(config.messages.empty_query_warning);
      return;
    }
    if (!Object.values(activeFlags).some(Boolean)) {
      setSearchError(config.messages.no_method_warning);
      return;
    }

    setLoading(true);
    setSearchError(null);
    try {
      const data = await searchProducts({
        query: trimmed,
        page,
        enablePhrase: activeFlags.enablePhrase,
        enableMultiMatch: activeFlags.enableMultiMatch,
        enableFuzzy: activeFlags.enableFuzzy,
        enableExactAsin: activeFlags.enableExactAsin,
      });
      setResult(data);
      setResultFlagsSig(flagsSignature(activeFlags));
      setCurrentPage(data.current_page);
      setEsOk(true);
    } catch (error) {
      setSearchError(error instanceof ApiError ? error.message : 'Beklenmeyen bir hata oluştu.');
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  const triggerExplicitSearch = (text) => {
    setQueryText(text.trim());
    setCurrentPage(1);
    executeSearch(text, 1, flags);
  };

  const handleExampleClick = (example) => {
    setSearchBoxValue(example);
    setSearchBoxRevision((r) => r + 1);
    triggerExplicitSearch(example);
  };

  const handleSelect = (item) => {
    setSearchBoxValue(item.title);
    setSearchBoxRevision((r) => r + 1);
    setSuggestions([]);
    triggerExplicitSearch(item.title);
  };

  const handleSubmit = (text) => {
    setSuggestions([]);
    triggerExplicitSearch(text);
  };

  const handleButtonClick = () => {
    setSuggestions([]);
    triggerExplicitSearch(searchBoxValue);
  };

  const handleTypingChange = async (text) => {
    setQueryText(text.trim());
    const trimmed = text.trim();
    const minChars = config.limits.autocomplete_min_chars;

    if (!liveSuggestions || !trimmed) {
      setSuggestions([]);
      setTypedTooShort(false);
      return;
    }
    if (trimmed.length < minChars) {
      setSuggestions([]);
      setTypedTooShort(true);
      return;
    }
    setTypedTooShort(false);

    try {
      const data = await getAutocomplete(trimmed);
      setSuggestions(data.suggestions);
      setSuggestionsError(false);
    } catch {
      setSuggestions([]);
      setSuggestionsError(true);
    }
  };

  const handlePrevPage = () => {
    const page = Math.max(1, currentPage - 1);
    setCurrentPage(page);
    executeSearch(result.query, page, flags);
    resultsTopRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const handleNextPage = () => {
    const page = currentPage + 1;
    setCurrentPage(page);
    executeSearch(result.query, page, flags);
    resultsTopRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  if (configError) {
    return (
      <div className="page">
        <div className="alert alert-error">{configError}</div>
      </div>
    );
  }

  if (!config) {
    return <div className="page page-loading">Yükleniyor…</div>;
  }

  const isStale = result && resultFlagsSig !== flagsSignature(flags);
  const showResults = result && !isStale && !searchError;

  return (
    <div className="page">
      <Hero hero={config.hero} />

      <div className="layout">
        <SidebarSettings
          config={config}
          flags={flags}
          onFlagsChange={setFlags}
          liveSuggestions={liveSuggestions}
          onLiveSuggestionsChange={setLiveSuggestions}
          esOk={esOk}
        />

        <main className="main-column">
          <div className="search-row">
            <SearchBox
              value={searchBoxValue}
              revision={searchBoxRevision}
              suggestions={liveSuggestions ? suggestions : []}
              placeholder={config.search_placeholder}
              debounceMs={config.debounce_ms}
              panelMaxHeightPx={config.autocomplete_ui.panel_max_height_px}
              rowHeightPx={config.autocomplete_ui.row_height_px}
              showImages={config.autocomplete_ui.show_images}
              onTypingChange={(text) => {
                setSearchBoxValue(text);
                handleTypingChange(text);
              }}
              onSubmit={handleSubmit}
              onSelect={handleSelect}
            />
            <button type="button" className="search-button" onClick={handleButtonClick}>
              {config.labels.search_button}
            </button>
          </div>

          <FeatureChips labels={config.labels} flags={flags} liveSuggestions={liveSuggestions} />

          {typedTooShort && liveSuggestions && (
            <p className="hint-caption">
              {formatMessage(config.messages.suggestions_min_chars_caption, {
                min_chars: config.limits.autocomplete_min_chars,
              })}
            </p>
          )}
          {suggestionsError && (
            <div className="alert alert-warning">{config.messages.suggestions_unavailable_warning}</div>
          )}

          {isStale && <div className="alert alert-info">{config.messages.settings_changed_info}</div>}

          {searchError && <div className="alert alert-error">{searchError}</div>}

          {loading && <div className="loading-caption">Ürünler aranıyor…</div>}

          {!showResults && !loading && !queryText && (
            <EmptyState
              labels={config.labels}
              exampleQueries={config.example_queries}
              onExampleClick={handleExampleClick}
            />
          )}

          {showResults && result.hits.length === 0 && (
            <div className="alert alert-info">
              {formatMessage(config.messages.no_results_info, { query: result.query })}
            </div>
          )}

          {showResults && result.hits.length > 0 && (
            <>
              <div ref={resultsTopRef} />
              <ResultHeader
                config={config}
                query={result.query}
                total={result.total}
                shown={result.hits.length}
                flags={flags}
                intent={result.intent}
              />
              <div className="product-list">
                {result.hits.map((product) => (
                  <ProductCard key={product.asin} product={product} />
                ))}
              </div>
              <Pagination config={config} result={result} onPrev={handlePrevPage} onNext={handleNextPage} />
            </>
          )}
        </main>
      </div>
    </div>
  );
}
