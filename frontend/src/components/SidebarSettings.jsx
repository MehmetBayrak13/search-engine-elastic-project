const TOGGLE_DEFS = [
  { key: 'enablePhrase', labelKey: 'toggle_phrase', helpKey: 'toggle_phrase' },
  { key: 'enableMultiMatch', labelKey: 'toggle_multi_match', helpKey: 'toggle_multi_match' },
  { key: 'enableFuzzy', labelKey: 'toggle_fuzzy', helpKey: 'toggle_fuzzy' },
  { key: 'enableExactAsin', labelKey: 'toggle_exact_asin', helpKey: 'toggle_exact_asin' },
];

export default function SidebarSettings({
  config,
  flags,
  onFlagsChange,
  liveSuggestions,
  onLiveSuggestionsChange,
  esOk,
}) {
  const { labels, help_text: helpText } = config;

  const toggleFlag = (key) => onFlagsChange({ ...flags, [key]: !flags[key] });

  return (
    <aside className="sidebar">
      <h3 className="sidebar-title">{labels.settings_panel_title}</h3>

      {TOGGLE_DEFS.map(({ key, labelKey, helpKey }) => (
        <div className="toggle-row" key={key}>
          <label>
            <input
              type="checkbox"
              checked={flags[key]}
              onChange={() => toggleFlag(key)}
            />
            <span>{labels[labelKey]}</span>
          </label>
          <p className="toggle-help">{helpText[helpKey]}</p>
        </div>
      ))}

      <hr />

      <div className="toggle-row">
        <label>
          <input
            type="checkbox"
            checked={liveSuggestions}
            onChange={(event) => onLiveSuggestionsChange(event.target.checked)}
          />
          <span>{labels.toggle_live_suggestions}</span>
        </label>
        <p className="toggle-help">{helpText.toggle_live_suggestions}</p>
      </div>

      <hr />

      <h4 className="sidebar-subtitle">{labels.system_status_title}</h4>
      <div className="status-caption">
        <div>Elasticsearch: {esOk ? labels.status_connected : labels.status_ready}</div>
        <div>Aranan index sayısı: {config.elasticsearch.search_index_count}</div>
        <div>Sonuç limiti: {config.limits.result_size}</div>
        <div>Öneri limiti: {config.limits.autocomplete_display_size}</div>
      </div>
    </aside>
  );
}
