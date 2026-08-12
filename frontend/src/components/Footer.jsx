export default function Footer({ indexCount }) {
  return (
    <footer className="footer">
      <b>{indexCount}</b> index &nbsp;·&nbsp; Elasticsearch destekli ürün arama motoru
    </footer>
  );
}
