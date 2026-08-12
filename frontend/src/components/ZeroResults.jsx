import { formatMessage } from '../lib/format';

export default function ZeroResults({ config, query, onExampleClick }) {
  const { labels, messages } = config;
  return (
    <div className="zero-state">
      <div className="es-icon">{labels.zero_state_icon || '🤔'}</div>
      <div className="es-title">{formatMessage(messages.no_results_info, { query })}</div>
      {labels.zero_state_tip && <div className="zero-tip">💡 {labels.zero_state_tip}</div>}
      {labels.zero_state_suggestions_label && <p className="es-text">{labels.zero_state_suggestions_label}</p>}
      <div className="example-row">
        {config.example_queries.map((example) => (
          <button key={example} type="button" className="example-chip" onClick={() => onExampleClick(example)}>
            {example}
          </button>
        ))}
      </div>
    </div>
  );
}
