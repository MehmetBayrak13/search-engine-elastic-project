/**
 * config'teki Python `str.format` tarzı mesaj şablonlarını ({key}, {key:,})
 * yorumlar. Backend'in `ui.message(...)` fonksiyonunun frontend karşılığı.
 */
export function formatMessage(template, vars = {}) {
  if (!template) return '';
  return template.replace(/\{(\w+)(:,)?\}/g, (match, key, comma) => {
    if (!(key in vars)) return match;
    const value = vars[key];
    if (comma && typeof value === 'number') return value.toLocaleString('en-US');
    return String(value);
  });
}
