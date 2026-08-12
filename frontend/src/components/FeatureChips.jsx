const FLAG_CHIP_KEYS = [
  { flagKey: 'enablePhrase', labelKey: 'chip_phrase' },
  { flagKey: 'enableMultiMatch', labelKey: 'chip_multi_match' },
  { flagKey: 'enableFuzzy', labelKey: 'chip_fuzzy' },
  { flagKey: 'enableExactAsin', labelKey: 'chip_exact_asin' },
];

/**
 * "Motor yetenek şeridi" — sidebar'daki arama ayarlarıyla canlı senkron
 * çalışan bir durum göstergesi. Aktif/kapalı yöntemleri filtrelemek yerine
 * HEPSİNİ her zaman gösterir; her chip'in LED noktası o yöntemin o an
 * açık mı kapalı mı olduğunu yansıtır.
 */
export default function FeatureChips({ labels, flags, liveSuggestions }) {
  const chips = FLAG_CHIP_KEYS.map(({ flagKey, labelKey }) => ({
    key: flagKey,
    label: labels[labelKey],
    on: Boolean(flags[flagKey]),
  }));
  chips.push({ key: 'live', label: labels.chip_live_suggestions, on: liveSuggestions });

  return (
    <div className="engine-strip" aria-label="Aktif arama yetenekleri">
      {chips.map((chip) => (
        <span key={chip.key} className={`engine-chip${chip.on ? '' : ' off'}`}>
          <span className="led" />
          {chip.label}
        </span>
      ))}
    </div>
  );
}
