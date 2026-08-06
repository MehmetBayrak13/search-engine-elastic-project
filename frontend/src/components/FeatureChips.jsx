const FLAG_CHIP_KEYS = {
  enablePhrase: 'chip_phrase',
  enableMultiMatch: 'chip_multi_match',
  enableFuzzy: 'chip_fuzzy',
  enableExactAsin: 'chip_exact_asin',
};

export default function FeatureChips({ labels, flags, liveSuggestions }) {
  const chips = Object.entries(FLAG_CHIP_KEYS)
    .filter(([key]) => flags[key])
    .map(([, chipKey]) => labels[chipKey]);
  if (liveSuggestions) chips.push(labels.chip_live_suggestions);

  return (
    <div className="chip-row">
      {chips.length > 0 ? (
        chips.map((text) => (
          <span key={text} className="chip chip-on">
            {text}
          </span>
        ))
      ) : (
        <span className="chip">{labels.chip_none || 'Aktif yöntem yok'}</span>
      )}
    </div>
  );
}
