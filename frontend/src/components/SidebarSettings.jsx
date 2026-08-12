const TOGGLE_DEFS = [
  { key: 'enablePhrase', labelKey: 'toggle_phrase', helpKey: 'toggle_phrase' },
  { key: 'enableMultiMatch', labelKey: 'toggle_multi_match', helpKey: 'toggle_multi_match' },
  { key: 'enableFuzzy', labelKey: 'toggle_fuzzy', helpKey: 'toggle_fuzzy' },
  { key: 'enableExactAsin', labelKey: 'toggle_exact_asin', helpKey: 'toggle_exact_asin' },
];

function Switch({ checked, onChange, label }) {
  return (
    <label className="switch">
      <input type="checkbox" checked={checked} onChange={onChange} aria-label={label} />
      <span className="slider" />
    </label>
  );
}

export default function SidebarSettings({
  config,
  flags,
  onFlagsChange,
  liveSuggestions,
  onLiveSuggestionsChange,
  esOk,
  lastLatencyMs,
}) {
  const { labels, help_text: helpText } = config;

  const toggleFlag = (key) => onFlagsChange({ ...flags, [key]: !flags[key] });

  return (
    <aside className="sidebar">
      <h3 className="sidebar-title">{labels.settings_panel_title}</h3>
      <p className="sidebar-caption">Değişiklikler bir sonraki aramada uygulanır.</p>

      {TOGGLE_DEFS.map(({ key, labelKey, helpKey }) => (
        <div className="toggle-row" key={key}>
          <div className="toggle-head">
            <span className="toggle-label">{labels[labelKey]}</span>
            <Switch checked={flags[key]} onChange={() => toggleFlag(key)} label={labels[labelKey]} />
          </div>
          <p className="toggle-help">{helpText[helpKey]}</p>
        </div>
      ))}

      <div className="toggle-row">
        <div className="toggle-head">
          <span className="toggle-label">{labels.toggle_live_suggestions}</span>
          <Switch
            checked={liveSuggestions}
            onChange={(event) => onLiveSuggestionsChange(event.target.checked)}
            label={labels.toggle_live_suggestions}
          />
        </div>
        <p className="toggle-help">{helpText.toggle_live_suggestions}</p>
      </div>

      <div className="sys-box">
        <div className="sys-title">{labels.system_status_title}</div>
        <div className="sys-line">
          <span>Elasticsearch</span>
          <b className={esOk ? 'ok' : ''}>● {esOk ? labels.status_connected : labels.status_ready}</b>
        </div>
        <div className="sys-line">
          <span>Aranan index</span>
          <b>{config.elasticsearch.search_index_count}</b>
        </div>
        <div className="sys-line">
          <span>Sonuç limiti</span>
          <b>{config.limits.result_size}</b>
        </div>
        <div className="sys-line">
          <span>Öneri limiti</span>
          <b>{config.limits.autocomplete_display_size}</b>
        </div>
        <div className="sys-line">
          <span>Son sorgu</span>
          <b>{lastLatencyMs != null ? `${lastLatencyMs} ms` : '—'}</b>
        </div>
      </div>
    </aside>
  );
}
