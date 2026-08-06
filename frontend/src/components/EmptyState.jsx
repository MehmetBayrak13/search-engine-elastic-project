export default function EmptyState({ labels, exampleQueries, onExampleClick }) {
  return (
    <div className="empty-state">
      <div className="es-icon">{labels.empty_state_icon || '🔍'}</div>
      <div className="es-text">{labels.empty_state_text}</div>
      <div className="example-row">
        {exampleQueries.map((example) => (
          <button
            key={example}
            type="button"
            className="example-chip"
            onClick={() => onExampleClick(example)}
          >
            {example}
          </button>
        ))}
      </div>
    </div>
  );
}
