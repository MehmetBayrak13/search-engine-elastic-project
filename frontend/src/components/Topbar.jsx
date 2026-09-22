import DownloadButton from './DownloadButton';

export default function Topbar({ hero, esOk, statusLabels = {}, onGoHome }) {
  return (
    <header className="topbar">
      <button type="button" className="brand" onClick={onGoHome} aria-label="Anasayfaya dön">
        <span className="brand-mark">{hero?.logo || '🛍️'}</span> Ürün Arama
      </button>
      <div className="topbar-status">
        <DownloadButton />
        <span className={`status-pill${esOk ? '' : ' status-pill-pending'}`}>
          <span className="dot" />
          {esOk ? statusLabels.status_connected : statusLabels.status_ready || 'Hazır'}
        </span>
      </div>
    </header>
  );
}
