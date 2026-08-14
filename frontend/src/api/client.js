const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, params = {}) {
  const url = new URL(API_BASE + path);
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.set(key, value);
    }
  });

  let response;
  try {
    response = await fetch(url);
  } catch {
    throw new ApiError('API sunucusuna ulaşılamadı. Backend çalışıyor mu?', 0);
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body.detail) detail = body.detail;
    } catch {
      // JSON olmayan hata gövdesi — statusText ile devam.
    }
    throw new ApiError(detail, response.status);
  }
  return response.json();
}

export function getConfig() {
  return request('/api/config');
}

export function getHealth() {
  return request('/api/health');
}

export function searchProducts({ query, page, enablePhrase, enableMultiMatch, enableFuzzy, enableExactAsin, sort }) {
  return request('/api/search', {
    q: query,
    page,
    enable_phrase: enablePhrase,
    enable_multi_match: enableMultiMatch,
    enable_fuzzy: enableFuzzy,
    enable_exact_asin: enableExactAsin,
    sort,
    // Ek bir Elasticsearch isteği YAPMAZ (bkz. api/main.py: relevance_debug_from_matched_queries
    // docstring'i) -- aynı yanıttan, sorguya eklenen `_name`'ler sayesinde okunur.
    // Kart başına "neden bu sonuç?" açıklaması için kullanılır.
    debug_relevance: true,
  });
}

export function getAutocomplete(query) {
  return request('/api/autocomplete', { q: query });
}

export { ApiError };
