import { formatMessage } from '../lib/format';

export default function Pagination({ config, result, onPrev, onNext }) {
  if (!config.pagination.enabled || !result || result.total_pages <= 1) return null;

  const { labels, messages } = config;

  return (
    <div className="pagination-bar">
      <p className="pagination-caption">
        {formatMessage(messages.pagination_range_indicator, {
          start: result.start_item,
          end: result.end_item,
        })}
      </p>
      <p className="pagination-caption">
        {formatMessage(messages.pagination_page_indicator, {
          current_page: result.current_page,
          total_pages: result.total_pages,
        })}
      </p>
      <div className="pagination-buttons">
        <button type="button" disabled={!result.has_previous} onClick={onPrev}>
          {labels.pagination_prev_button || '← Önceki'}
        </button>
        <button type="button" disabled={!result.has_next} onClick={onNext}>
          {labels.pagination_next_button || 'Sonraki →'}
        </button>
      </div>
      {result.total > config.pagination.max_result_window && (
        <p className="pagination-caption">
          {formatMessage(messages.pagination_window_notice, {
            max_result_window: config.pagination.max_result_window,
          })}
        </p>
      )}
    </div>
  );
}
