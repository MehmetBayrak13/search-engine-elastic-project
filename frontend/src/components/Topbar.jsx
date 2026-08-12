export default function Topbar({ hero, esOk, statusLabels }) {
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">{hero?.logo || '🛍️'}</span> Ürün Arama
      </div>
      <div className="topbar-status">
        <span className={`status-pill${esOk ? '' : ' status-pill-pending'}`}>
          <span className="dot" />
          {esOk ? statusLabels.status_connected : statusLabels.status_ready}
        </span>
      </div>
    </header>
  );
}
