import { formatMessage } from '../lib/format';

export default function ResultHeader({ config, query, total, shown, flags, intent }) {
  const { labels, messages } = config;
  const flagLabelKeys = {
    enablePhrase: 'method_phrase',
    enableMultiMatch: 'method_multi_match',
    enableFuzzy: 'method_fuzzy',
    enableExactAsin: 'method_exact_asin',
  };
  const methods =
    Object.entries(flagLabelKeys)
      .filter(([key]) => flags[key])
      .map(([, labelKey]) => labels[labelKey])
      .join(', ') || 'yok';

  return (
    <div className="result-header">
      <div className="rh-query">{formatMessage(messages.result_header_query, { query, total })}</div>
      <div className="rh-meta">{formatMessage(messages.result_header_meta, { shown, methods })}</div>
      {intent && (
        <div className="intent-badge">
          {intent.icon} {intent.label}
        </div>
      )}
    </div>
  );
}
