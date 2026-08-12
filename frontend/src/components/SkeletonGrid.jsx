export default function SkeletonGrid({ count = 8 }) {
  return (
    <div className="grid">
      {Array.from({ length: count }, (_, index) => (
        // eslint-disable-next-line react/no-array-index-key
        <div className="sk-card" key={index}>
          <div className="sk sk-media" />
          <div className="sk sk-line w40" />
          <div className="sk sk-line w85" />
          <div className="sk sk-line w60" />
        </div>
      ))}
    </div>
  );
}
