import download from '../download.json';

export default function DownloadButton() {
  return (
    <div className="download-entry">
      <a className="download-button" href={`/downloads/${download.filename}`} download={download.filename}
        aria-label="CV Eşleştirme Windows ZIP paketini indir">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
          <path d="M12 3v12m-5-5 5 5 5-5M5 16v5h14v-5" />
        </svg>
        Download
      </a>
      <span className="download-caption">CV Eşleştirme · Windows ZIP · Docker Desktop gerekli</span>
    </div>
  );
}
