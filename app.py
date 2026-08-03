"""
Ürün Arama — Elastic Cloud destekli, tıklanabilir kartlı e-ticaret arama arayüzü.

Çalıştırma:
    pip install streamlit requests

    # Linux / macOS:
    #   export ELASTICSEARCH_URL="https://<deployment>.es.<region>.cloud.es.io"
    #   export ELASTICSEARCH_API_KEY="<api_key>"
    # Windows (PowerShell):
    #   $env:ELASTICSEARCH_URL="https://<deployment>.es.<region>.cloud.es.io"
    #   $env:ELASTICSEARCH_API_KEY="<api_key>"

    streamlit run app.py
"""

import html
import os
import re

import requests
import streamlit as st
import streamlit.components.v1 as components

# streamlit-keyup: gerçek tuş-bazlı (debounce'lı) canlı input. Kurulu değilse
# uygulama düz st.text_input'a düşer (bozulmaz), ancak canlılık azalır.
try:
    from st_keyup import st_keyup
    HAS_KEYUP = True
except Exception:  # ImportError ve olası kurulum sorunları
    HAS_KEYUP = False

# ---------------------------------------------------------------------------
# Ayarlar
# ---------------------------------------------------------------------------
ES_URL = os.getenv("ELASTICSEARCH_URL")
ES_API_KEY = os.getenv("ELASTICSEARCH_API_KEY")
if ES_URL:
    ES_URL = ES_URL.rstrip("/")

# Yalnızca ana indexlerde arama yapılır (test ve v1 dahil edilmez).
INDEX_NAME = "amazon-products-000001,amazon-products-000002"
# Canlı öneriler ayrı bir Edge NGram test indexinden gelir.
AUTOCOMPLETE_INDEX_NAME = "amazon-products-autocomplete-test"
RESULT_SIZE = 20

# Tam ürün aramasında yanıtta dönen alanlar.
SOURCE_FIELDS = [
    "parent_asin",
    "title",
    "main_category",
    "categories",
    "source_category",
    "store",
    "price",
    "average_rating",
    "rating_number",
    "description",
    "features",
    "image_url",
]

# Canlı öneri (autocomplete) sorgusunda yanıtta dönen alanlar.
SUGGESTION_SOURCE_FIELDS = [
    "parent_asin",
    "title",
    "store",
    "price",
    "average_rating",
    "rating_number",
    "image_url",
    "main_category",
    "categories",
    "source_category",
]
PLACEHOLDER_IMAGE = "https://via.placeholder.com/160x160.png?text=G%C3%B6rsel+Yok"

st.set_page_config(page_title="Ürün Arama", page_icon="🔍", layout="wide")

# ---------------------------------------------------------------------------
# Sayfa CSS — modern hero, chip'ler, grid ve boş durum
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
        .block-container { max-width: 1180px; padding-top: 2rem; }

        /* Hero bölümü */
        .hero {
            text-align: center;
            padding: 1.2rem 0 0.4rem 0;
        }
        .hero-logo {
            font-size: 2.4rem;
            line-height: 1;
            margin-bottom: 0.4rem;
        }
        .hero-title {
            font-size: 2.1rem;
            font-weight: 800;
            letter-spacing: -0.02em;
            margin: 0 0 0.35rem 0;
        }
        .hero-subtitle {
            opacity: 0.7;
            font-size: 1rem;
            margin: 0 auto 0.4rem auto;
            max-width: 620px;
        }

        /* Aktif özellik chip'leri */
        .chip-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 0.4rem 0 0.2rem 0; }
        .chip {
            display: inline-flex; align-items: center; gap: 6px;
            font-size: 0.78rem; font-weight: 600;
            padding: 4px 12px; border-radius: 999px;
            border: 1px solid var(--chip-border, #d1d5db);
            background: var(--chip-bg, #f3f4f6);
            color: var(--chip-fg, #374151);
        }
        .chip-on {
            border-color: #c7d2fe; background: #eef2ff; color: #4338ca;
        }

        /* Sonuç üst bilgisi */
        .result-header {
            margin: 1.2rem 0 0.6rem 0;
        }
        .result-header .rh-query { font-size: 1.15rem; font-weight: 700; }
        .result-header .rh-meta { font-size: 0.9rem; opacity: 0.7; margin-top: 2px; }
        .intent-badge {
            display: inline-block; margin-top: 6px;
            font-size: 0.8rem; font-weight: 600;
            padding: 3px 10px; border-radius: 999px;
            background: #ecfdf5; color: #047857; border: 1px solid #a7f3d0;
        }

        /* Öneri paneli başlığı */
        .suggest-panel-title {
            font-size: 0.95rem; font-weight: 700; margin: 0.4rem 0 0.2rem 0;
        }

        /* Boş durum */
        .empty-state {
            text-align: center;
            opacity: 0.75;
            padding: 2.5rem 0 1rem 0;
        }
        .empty-state .es-icon { font-size: 2.6rem; }
        .empty-state .es-text { font-size: 1rem; opacity: 0.7; margin-top: 0.3rem; }

        /* Örnek sorgu chip butonları (Streamlit butonlarını sadeleştir) */
        div[data-testid="stButton"] > button {
            border-radius: 10px;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Yapılandırma kontrolü
# ---------------------------------------------------------------------------
def check_configuration():
    """Gerekli ortam değişkenlerini kontrol eder; eksikse hata mesajı döner."""
    missing = []
    if not ES_URL:
        missing.append("ELASTICSEARCH_URL")
    if not ES_API_KEY:
        missing.append("ELASTICSEARCH_API_KEY")

    if missing:
        return (
            "Elastic Cloud bağlantı bilgileri eksik: "
            f"**{', '.join(missing)}** ortam değişken(ler)i tanımlı değil. "
            "Uygulamayı başlatmadan önce bu değişkenleri ayarlayıp yeniden başlatın."
        )
    return None


# ---------------------------------------------------------------------------
# Niyet (intent) tespiti — kategori farkındalıklı reranking
# ---------------------------------------------------------------------------
# Kolay genişletilebilir kural yapısı. İlk aşamada yalnızca "watch" gerçek uygulanır.
INTENT_RULES = {
    "watch": {
        # Bu terimlerden biri sorguda geçerse niyet algılanır.
        "query_terms": [
            "watch", "watches", "wristwatch", "wrist watch",
            "smartwatch", "smart watch", "kol saati", "akıllı saat", "saat",
        ],
        # Sorguda bu terimlerden biri geçerse niyet iptal edilir (dışlama yapılmaz).
        "excluded_when_query_contains": [
            "book", "books", "novel", "kitabı", "kitap",
        ],
        # Kategori sinyali olarak boostlanacak terimler (text alanlarında).
        "category_boost_terms": [
            "watches", "smartwatch", "wearable technology",
            "wrist watches", "men's watches", "women's watches",
        ],
        # Geri plana atılacak / dışlanabilecek kategori terimleri.
        "negative_categories": ["books", "book"],
    },
}


def detect_search_intent(query_text: str) -> dict:
    """
    Sorgu metninden kategori niyetini tespit eder (casefold ile normalize).

    Dönüş:
      {"intent": <ad|None>, "apply_exclusion": <bool>, "rule": <kural|None>}

    Kural: niyet terimlerinden biri geçiyorsa niyet algılanır. Ancak sorguda
    dışlama tetikleyici bir terim (ör. "book") de varsa niyet algılanır fakat
    kitap dışlaması UYGULANMAZ ("watch book" → kitaplar dışlanmaz).
    """
    text = (query_text or "").casefold()

    for name, rule in INTENT_RULES.items():
        # Niyet terimi kelime/ifade olarak geçiyor mu?
        hit = any(_contains_term(text, t) for t in rule["query_terms"])
        if not hit:
            continue

        # Dışlama tetikleyici terim var mı? Varsa dışlama uygulanmaz.
        blocked = any(
            _contains_term(text, t) for t in rule["excluded_when_query_contains"]
        )
        return {
            "intent": name,
            "apply_exclusion": not blocked,
            "rule": rule,
        }

    return {"intent": None, "apply_exclusion": False, "rule": None}


def _contains_term(text: str, term: str) -> bool:
    """
    `term`'in `text` içinde kelime sınırıyla geçip geçmediğini kontrol eder
    (casefold edilmiş metin beklenir). Çok kelimeli terimler alt-dizi olarak aranır.
    """
    term = term.casefold()
    if " " in term:
        return term in text
    # Tek kelime: kelime sınırı ile ara (ör. "watch" -> "watchband" eşleşmesin).
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None


def _build_intent_signals(query_text: str):
    """
    Sorgudan watch-intent boost ve dışlama sorgularını üretir (paylaşılan mantık;
    hem normal arama hem autocomplete önerileri kullanır).

    Dönüş: (intent_boost_queries, intent_exclusions)
    Bu sorgular tek başına belge döndürmez; yalnızca ana eşleşmenin üzerine
    reranking/dışlama sinyali ekler.
    """
    intent_boost_queries = []
    intent_exclusions = []
    info = detect_search_intent(query_text)
    rule = info.get("rule")
    if rule:
        for term in rule["category_boost_terms"]:
            intent_boost_queries.append({
                "match_phrase": {"categories_text": {"query": term, "boost": 12}}
            })
            intent_boost_queries.append({
                "match_phrase": {"categories_text.tr": {"query": term, "boost": 8}}
            })
        if info.get("apply_exclusion"):
            # Kitap belgeleri bazen yalnızca main_category/source_category
            # üzerinden "Books" taşıyor (categories/categories_text boş olabiliyor).
            # Bu yüzden dışlamayı tüm ilgili alanlara uygula: keyword alanlarda
            # term, text alanlarda match_phrase. Her terim için bir bool.should
            # (minimum_should_match=1) bloğu must_not'a eklenir.
            for term in rule["negative_categories"]:
                intent_exclusions.append({
                    "bool": {
                        "should": [
                            {"term": {"main_category": term}},
                            {"term": {"source_category": term}},
                            {"term": {"categories": term}},
                            {"match_phrase": {"categories_text": {"query": term}}},
                            {"match_phrase": {"categories_text.tr": {"query": term}}},
                        ],
                        "minimum_should_match": 1,
                    }
                })
    return intent_boost_queries, intent_exclusions


def build_autocomplete_query(
    query_text: str,
    result_size: int = 15,
    apply_intent_reranking: bool = True,
) -> dict:
    """
    Canlı öneriler için gerçek Edge NGram test indexi sorgusu.

    Normal lexical yöntemlerden (phrase/multi/fuzzy/asin switchleri) bağımsızdır:
    zorunlu eşleşme yalnızca `title.autocomplete` alanı üzerindendir (operator=and).
    watch-intent korunur: saat kategorileri `should` içinde boostlanır, kitap
    niyeti yoksa kitap kategorileri `must_not` ile dışlanır. Intent sinyalleri
    tek başına sonuç üretmez; autocomplete eşleşmesi zorunludur.
    """
    intent_boost_queries = []
    intent_exclusions = []
    if apply_intent_reranking:
        intent_boost_queries, intent_exclusions = _build_intent_signals(query_text)

    return {
        "size": result_size,
        "track_total_hits": False,
        "timeout": "10s",
        "_source": SUGGESTION_SOURCE_FIELDS,
        "query": {
            "bool": {
                "must": [
                    {
                        "match": {
                            "title.autocomplete": {
                                "query": query_text,
                                "operator": "and",
                            }
                        }
                    }
                ],
                "should": intent_boost_queries,
                "must_not": intent_exclusions,
            }
        },
    }


# ---------------------------------------------------------------------------
# Sorgu oluşturma (normal arama)
# ---------------------------------------------------------------------------
def build_search_query(
    query_text: str,
    enable_phrase: bool = True,
    enable_multi_match: bool = True,
    enable_fuzzy: bool = True,
    enable_exact_asin: bool = True,
    result_size: int = RESULT_SIZE,
    track_total_hits: bool = True,
    apply_intent_reranking: bool = True,
) -> dict:
    """
    Seçili yöntemlere göre intent-farkındalıklı bir Elasticsearch sorgusu üretir.

    Lexical yöntemler (aç/kapa):
      A) parent_asin exact `term`   (enable_exact_asin)
      B) title `match_phrase`        (enable_phrase)
      C) full-text `multi_match`     (enable_multi_match)
      D) fuzzy `multi_match`         (enable_fuzzy)

    Yapı:
      bool.must   → [ bool.should=lexical, minimum_should_match=1 ]  (zorunlu eşleşme)
      bool.should → intent kategori boostları (yalnızca sıralamayı iyileştirir)
      bool.must_not → intent dışlamaları (kontrollü)

    Böylece kategori boostları tek başına belge döndürmez; ürün önce lexical
    olarak eşleşmek zorundadır. Hiç lexical yöntem yoksa `match_none` döner.
    """
    lexical_queries = []

    # A. Exact ürün kodu eşleşmesi (keyword alan → term)
    if enable_exact_asin:
        lexical_queries.append({
            "term": {
                "parent_asin": {
                    "value": query_text,
                    "boost": 25,
                    "case_insensitive": True,
                }
            }
        })

    # B. Tam ifadeye yakın başlık eşleşmesi
    if enable_phrase:
        lexical_queries.append({
            "match_phrase": {
                "title": {
                    "query": query_text,
                    "boost": 10,
                }
            }
        })

    # C. Normal full-text multi_match
    if enable_multi_match:
        lexical_queries.append({
            "multi_match": {
                "query": query_text,
                "type": "best_fields",
                "operator": "and",
                "boost": 4,
                "fields": [
                    "title^7",
                    "title.tr^6",
                    "categories_text^3",
                    "categories_text.tr^3",
                    "description^1.5",
                    "description.tr^1.5",
                    "features^1.5",
                    "features.tr^1.5",
                ],
            }
        })

    # D. Fuzzy multi_match (yazım hataları)
    if enable_fuzzy:
        lexical_queries.append({
            "multi_match": {
                "query": query_text,
                "type": "best_fields",
                "fuzziness": "AUTO",
                "prefix_length": 2,
                "max_expansions": 30,
                "boost": 1,
                "fields": [
                    "title^6",
                    "title.tr^5",
                    "categories_text^2",
                    "categories_text.tr^2",
                ],
            }
        })

    # Hiç lexical yöntem yoksa güvenlik ağı.
    if not lexical_queries:
        return {
            "size": result_size,
            "track_total_hits": track_total_hits,
            "_source": SOURCE_FIELDS,
            "query": {"match_none": {}},
        }

    # Intent boost/dışlama sinyalleri (lexical eşleşmeyi zorunlu bırakır).
    intent_boost_queries = []
    intent_exclusions = []
    if apply_intent_reranking:
        intent_boost_queries, intent_exclusions = _build_intent_signals(query_text)

    bool_query = {
        "must": [
            {
                "bool": {
                    "should": lexical_queries,
                    "minimum_should_match": 1,
                }
            }
        ],
    }
    if intent_boost_queries:
        bool_query["should"] = intent_boost_queries
    if intent_exclusions:
        bool_query["must_not"] = intent_exclusions

    return {
        "size": result_size,
        "track_total_hits": track_total_hits,
        "_source": SOURCE_FIELDS,
        "query": {"bool": bool_query},
    }


# ---------------------------------------------------------------------------
# Elasticsearch isteği
# ---------------------------------------------------------------------------
def _post_search(payload: dict, timeout: int = 20, index: str = INDEX_NAME):
    """
    Verilen sorgu gövdesini belirtilen index üzerinde Elastic Cloud'a gönderir;
    ortak HTTP ve hata yönetimini tek yerde toplar. Varsayılan index normal
    aramanın kullandığı INDEX_NAME'dir.

    Dönüş: (data_dict, hata_mesaji). Hata varsa data_dict None döner.
    """
    headers = {
        "Authorization": f"ApiKey {ES_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            f"{ES_URL}/{index}/_search",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except requests.exceptions.ConnectionError:
        return None, (
            "Elastic Cloud'a bağlanılamadı. ELASTICSEARCH_URL değerinin doğru "
            "olduğundan ve internet bağlantınızın çalıştığından emin olun."
        )
    except requests.exceptions.Timeout:
        return None, "Elasticsearch isteği zaman aşımına uğradı. Lütfen tekrar deneyin."
    except requests.exceptions.RequestException:
        return None, "Arama sırasında beklenmeyen bir hata oluştu."

    if response.status_code == 401:
        return None, (
            "Kimlik doğrulama başarısız (401): API key geçersiz veya eksik. "
            "ELASTICSEARCH_API_KEY değerini kontrol edin."
        )
    if response.status_code == 403:
        return None, (
            "Yetki hatası (403): API key'in "
            f"`{index}` index'i üzerinde okuma yetkisi yok."
        )
    if response.status_code == 404:
        return None, (
            f"Index bulunamadı (404): `{index}` bulunamadı. "
            "Index adlarını ve deployment'ı kontrol edin."
        )
    if response.status_code >= 400:
        # Elasticsearch hata gövdesini güvenli biçimde mesaja ekle.
        detail = ""
        try:
            body = response.json()
            reason = (
                body.get("error", {}).get("reason")
                if isinstance(body.get("error"), dict)
                else body.get("error")
            )
            detail = f" — {reason}" if reason else f" — {str(body)[:300]}"
        except ValueError:
            detail = f" — {response.text[:300]}" if response.text else ""
        return None, f"Elasticsearch bir hata döndürdü: {response.status_code}{detail}"

    try:
        return response.json(), None
    except ValueError:
        return None, "Elasticsearch yanıtı çözümlenemedi."


def search_products(
    query_text: str,
    enable_phrase: bool = True,
    enable_multi_match: bool = True,
    enable_fuzzy: bool = True,
    enable_exact_asin: bool = True,
):
    """
    Seçili yöntemlerle üretilen gelişmiş sorguyu Elastic Cloud'a gönderir.

    Dönüş: (hits_listesi, toplam_sonuc, hata_mesaji)
    Hata varsa hits_listesi None döner.
    """
    payload = build_search_query(
        query_text,
        enable_phrase=enable_phrase,
        enable_multi_match=enable_multi_match,
        enable_fuzzy=enable_fuzzy,
        enable_exact_asin=enable_exact_asin,
        result_size=RESULT_SIZE,
        track_total_hits=True,
    )

    data, error = _post_search(payload, timeout=20)
    if error:
        return None, 0, error

    hits = data.get("hits", {}).get("hits", [])
    total = data.get("hits", {}).get("total", {}).get("value", len(hits))
    return hits, total, None


@st.cache_data(ttl=30, show_spinner=False)
def _fetch_suggestion_hits(query_text: str, result_size: int):
    """
    Canlı önerileri gerçek Edge NGram test indexinden (AUTOCOMPLETE_INDEX_NAME)
    getiren cache'li katman. 30 sn TTL. Cache anahtarı yalnızca query_text ve
    result_size'dır; lexical arama switchleri önerileri ETKİLEMEZ.

    Cache güvenliği için yalnızca JSON-benzeri argümanlar/dönüşler kullanılır;
    API key ve bağlantı nesneleri parametre yapılmaz (ortam değişkeni globaldir).

    Dönüş: (hits_listesi, hata_mesaji)
    """
    payload = build_autocomplete_query(query_text, result_size=result_size)

    data, error = _post_search(
        payload, timeout=10, index=AUTOCOMPLETE_INDEX_NAME
    )
    if error:
        return [], error
    return data.get("hits", {}).get("hits", []), None


def get_suggestions(query_text: str, max_items: int = 5):
    """
    Gerçek Edge NGram autocomplete önerileri: title.autocomplete alanı üzerinden
    _score sıralı ürün önerileri (watch-intent boost/dışlama korunur).

    Elasticsearch'ten 15 hit istenir, Python'da benzersizleştirilir (parent_asin,
    yoksa title.casefold()) ve en fazla `max_items` öneri döndürülür. Öneriler
    lexical arama switchlerinden bağımsızdır.

    Dönüş: (suggestions, error)
      - başarılı: (liste, None)
      - hata:     ([], "hata mesajı")
    """
    hits, error = _fetch_suggestion_hits(query_text, 15)
    if error:
        return [], error

    suggestions = []
    seen = set()
    for hit in hits:
        src = hit.get("_source", {})
        asin = src.get("parent_asin")
        title = src.get("title")

        key = asin if asin else (title.casefold() if title else None)
        if key is None or key in seen:
            continue
        seen.add(key)

        suggestions.append({
            "title": title or "Ürün adı bulunamadı",
            "asin": asin or "",
            "store": src.get("store"),
            "price": src.get("price"),
            "average_rating": src.get("average_rating"),
            "image_url": src.get("image_url"),
            "score": hit.get("_score", 0) or 0,
        })
        if len(suggestions) >= max_items:
            break

    return suggestions, None


# ---------------------------------------------------------------------------
# Tıklanabilir kart (components.v1.html ile)
# ---------------------------------------------------------------------------
def render_product_card(hit: dict):
    """
    Tek bir ürün kartını tıklanabilir bir HTML bileşeni olarak render eder.
    Kartın tamamı link gibi davranır; tıklama yeni sekmede Amazon ürününü açar.
    """
    source = hit.get("_source", {})
    score = hit.get("_score", 0) or 0

    # ASIN: önce parent_asin, yoksa _id
    asin = source.get("parent_asin") or hit.get("_id") or ""
    product_url = f"https://www.amazon.com/dp/{asin}" if asin else ""

    title = source.get("title") or "Ürün adı bulunamadı"
    store = source.get("store") or "Bilinmiyor"
    main_category = source.get("main_category") or "Kategori yok"
    average_rating = source.get("average_rating")
    rating_number = source.get("rating_number")
    price = source.get("price")
    image_url = source.get("image_url") or PLACEHOLDER_IMAGE

    # Tüm değerleri güvenli hale getir
    e_url = html.escape(product_url, quote=True)
    e_title = html.escape(str(title))
    e_store = html.escape(str(store))
    e_category = html.escape(str(main_category))
    e_image = html.escape(str(image_url), quote=True)
    e_placeholder = html.escape(PLACEHOLDER_IMAGE, quote=True)

    if average_rating is not None:
        rating_text = (
            f"⭐ {html.escape(str(average_rating))} &nbsp;·&nbsp; "
            f"{html.escape(str(rating_number or 0))} değerlendirme"
        )
    else:
        rating_text = "Değerlendirme yok"

    score_text = f"Skor: {score:.2f}"

    # Fiyat: yalnızca geçerli bir sayı varsa göster, yoksa hata verme.
    price_html = ""
    try:
        if price is not None and float(price) > 0:
            price_html = f'<div class="product-price">💲 {float(price):.2f}</div>'
    except (TypeError, ValueError):
        price_html = ""

    # JS onclick içine güvenli biçimde gömmek için tek tırnak kaçışı
    js_url = e_url.replace("'", "\\'")

    card_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ background: transparent; font-family: -apple-system, BlinkMacSystemFont,
                "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}

        .product-card {{
            display: flex;
            gap: 20px;
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            padding: 18px;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
            transition: box-shadow 0.2s ease, transform 0.2s ease;
            cursor: pointer;
            text-decoration: none;
            outline: none;
        }}
        .product-card:hover {{
            box-shadow: 0 6px 18px rgba(0, 0, 0, 0.14);
            transform: translateY(-2px);
        }}
        .product-card:focus-visible {{
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.5);
        }}

        .product-image {{
            width: 140px;
            height: 140px;
            flex-shrink: 0;
            border-radius: 8px;
            object-fit: contain;
            background: #f9fafb;
            border: 1px solid #f3f4f6;
        }}

        .product-info {{ flex: 1; min-width: 0; }}
        /* Başlık düz koyu metin — link değil, alt çizgi/mavi renk YOK */
        .product-title {{
            font-size: 1.05rem;
            font-weight: 600;
            color: #111827;
            margin-bottom: 8px;
            line-height: 1.4;
            text-decoration: none;
        }}
        .product-meta {{
            color: #6b7280;
            font-size: 0.88rem;
            margin-bottom: 4px;
        }}
        .product-rating {{
            color: #374151;
            font-size: 0.9rem;
            margin-top: 6px;
        }}
        .product-price {{
            color: #059669;
            font-weight: 600;
            font-size: 0.95rem;
            margin-top: 4px;
        }}
        .score-badge {{
            display: inline-block;
            background: #f3f4f6;
            color: #4b5563;
            font-size: 0.78rem;
            padding: 2px 10px;
            border-radius: 999px;
            margin-top: 8px;
        }}
    </style>
    </head>
    <body>
        <div class="product-card" role="link" tabindex="0"
             aria-label="{e_title}"
             onclick="openProduct()"
             onkeydown="if(event.key==='Enter'||event.key===' '){{event.preventDefault();openProduct();}}">
            <img class="product-image" src="{e_image}"
                 onerror="this.onerror=null;this.src='{e_placeholder}'" alt="Ürün görseli">
            <div class="product-info">
                <div class="product-title">{e_title}</div>
                <div class="product-meta">🏬 Mağaza: {e_store}</div>
                <div class="product-meta">📂 Kategori: {e_category}</div>
                <div class="product-rating">{rating_text}</div>
                {price_html}
                <span class="score-badge">{score_text}</span>
            </div>
        </div>
        <script>
            function openProduct() {{
                var url = '{js_url}';
                if (url) {{ window.open(url, "_blank", "noopener,noreferrer"); }}
            }}
        </script>
    </body>
    </html>
    """

    # Kart yüksekliği: 140px görsel + iç boşluk + (varsa) fiyat satırı + kart altı boşluk.
    card_height = 224 if price_html else 196
    components.html(card_html, height=card_height, scrolling=False)


# ---------------------------------------------------------------------------
# Arayüz yardımcıları
# ---------------------------------------------------------------------------
def _suggestion_visual_html(item: dict) -> tuple:
    """Öneri satırının sol görsel kısmını (kompakt kart) HTML olarak üretir."""
    title = item.get("title") or "Ürün adı bulunamadı"
    store = item.get("store") or "Mağaza bilgisi yok"
    price = item.get("price")
    rating = item.get("average_rating")
    image_url = item.get("image_url") or PLACEHOLDER_IMAGE
    score = item.get("score", 0) or 0

    e_title = html.escape(str(title))
    e_store = html.escape(str(store))
    e_image = html.escape(str(image_url), quote=True)
    e_placeholder = html.escape(PLACEHOLDER_IMAGE, quote=True)

    price_html = ""
    try:
        if price is not None and float(price) > 0:
            price_html = f'<span class="s-price">💲 {float(price):.2f}</span>'
    except (TypeError, ValueError):
        price_html = ""

    rating_html = ""
    if rating is not None:
        rating_html = f'<span class="s-rating">⭐ {html.escape(str(rating))}</span>'

    meta_line = " · ".join(
        p for p in [f"🏬 {e_store}", rating_html, price_html] if p
    )

    card_html = f"""
    <!DOCTYPE html>
    <html><head><meta charset="utf-8"><style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ background: transparent; font-family: -apple-system, BlinkMacSystemFont,
                "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
        .s-card {{
            display: flex; gap: 12px; align-items: center;
            background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px;
            padding: 8px 12px;
        }}
        .s-image {{
            width: 48px; height: 48px; flex-shrink: 0; object-fit: contain;
            border-radius: 6px; background: #f9fafb; border: 1px solid #f3f4f6;
        }}
        .s-info {{ flex: 1; min-width: 0; }}
        .s-title {{
            font-size: 0.9rem; font-weight: 600; color: #111827; line-height: 1.3;
            display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical;
            overflow: hidden;
        }}
        .s-metaline {{ font-size: 0.76rem; color: #6b7280; margin-top: 2px; }}
        .s-price {{ color: #059669; font-weight: 600; }}
        .s-rating {{ color: #374151; }}
        .s-score {{
            display: inline-block; background: #f3f4f6; color: #4b5563;
            font-size: 0.68rem; padding: 1px 8px; border-radius: 999px; margin-top: 3px;
        }}
    </style></head><body>
        <div class="s-card">
            <img class="s-image" src="{e_image}"
                 onerror="this.onerror=null;this.src='{e_placeholder}'" alt="">
            <div class="s-info">
                <div class="s-title">{e_title}</div>
                <div class="s-metaline">{meta_line}</div>
                <span class="s-score">Arama skoru: {score:.2f}</span>
            </div>
        </div>
    </body></html>
    """
    return card_html, 88


def render_suggestion_row(item: dict, idx: int) -> bool:
    """
    Tek bir öneriyi kompakt biçimde çizer: solda görsel/bilgi, sağda iki eylem.
      - "Bu ürünü ara": inputu bu başlıkla doldurup normal aramayı çalıştırır.
      - "Amazon'da aç": parent_asin varsa yeni sekmede ürün sayfasını açar.
    Dönüş: "Bu ürünü ara" tıklandıysa True.
    """
    card_html, height = _suggestion_visual_html(item)
    col_card, col_actions = st.columns([4, 1.15])
    with col_card:
        components.html(card_html, height=height, scrolling=False)
    with col_actions:
        picked = st.button(
            "🔎 Bu ürünü ara", key=f"pick_{idx}", use_container_width=True
        )
        asin = item.get("asin") or ""
        if asin:
            amazon_url = f"https://www.amazon.com/dp/{asin}"
            if hasattr(st, "link_button"):
                st.link_button(
                    "Amazon'da aç", amazon_url, use_container_width=True
                )
            else:
                # Güvenli HTML anchor (yeni sekme).
                safe_url = html.escape(amazon_url, quote=True)
                st.markdown(
                    f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">'
                    "Amazon'da aç</a>",
                    unsafe_allow_html=True,
                )
    return picked


def _toggle(label: str, value: bool, key: str, help_text: str = "") -> bool:
    """st.toggle varsa onu, yoksa st.checkbox'ı kullanır (sürüm uyumu)."""
    if hasattr(st, "toggle"):
        val = st.sidebar.toggle(label, value=value, key=key)
    else:
        val = st.sidebar.checkbox(label, value=value, key=key)
    if help_text:
        st.sidebar.caption(help_text)
    return val


def render_search_settings():
    """
    Sidebar'daki "Arama Ayarları" paneli: her switch'in altında bir açıklama
    ve altta küçük bir sistem durumu alanı. Dönüş: (flags, live_suggestions)
    """
    st.sidebar.markdown("### 🔧 Arama Ayarları")
    enable_phrase = _toggle(
        "İfade eşleşmesi", True, "opt_phrase",
        "Kelimelerin başlıktaki sırasını ödüllendirir.",
    )
    enable_multi = _toggle(
        "Çok alanlı arama", True, "opt_multi",
        "Başlık, kategori, açıklama ve özelliklerde arar.",
    )
    enable_fuzzy = _toggle(
        "Yazım hatası toleransı", True, "opt_fuzzy",
        "Yanlış yazılmış kelimelere yakın sonuçlar bulur.",
    )
    enable_asin = _toggle(
        "ASIN önceliği", True, "opt_asin",
        "Tam ürün kodu eşleşmesini en üste taşır.",
    )
    st.sidebar.markdown("---")
    live_suggestions = _toggle(
        "Canlı öneriler", True, "opt_suggest",
        "Yazarken en alakalı 5 ürünü gösterir.",
    )

    flags = {
        "enable_phrase": enable_phrase,
        "enable_multi_match": enable_multi,
        "enable_fuzzy": enable_fuzzy,
        "enable_exact_asin": enable_asin,
    }

    # Sistem durumu (gerçek bağlantı doğrulanmadan "Bağlı" denmez).
    st.sidebar.markdown("---")
    st.sidebar.markdown("#### Sistem Durumu")
    es_state = "Bağlı" if st.session_state.get("es_ok") else "Hazır"
    st.sidebar.caption(
        f"Elasticsearch: {es_state}  \n"
        "Aranan index sayısı: 2  \n"
        f"Sonuç limiti: {RESULT_SIZE}  \n"
        "Öneri limiti: 5"
    )
    return flags, live_suggestions


# İngilizce chip etiketleri (üst bölüm) ve Türkçe yöntem etiketleri (sonuç başlığı).
_CHIP_LABELS = {
    "enable_phrase": "Phrase",
    "enable_multi_match": "Multi-field",
    "enable_fuzzy": "Fuzzy",
    "enable_exact_asin": "Exact ASIN",
}
_METHOD_LABELS_TR = {
    "enable_phrase": "İfade eşleşmesi",
    "enable_multi_match": "Çok alanlı arama",
    "enable_fuzzy": "Yazım hatası toleransı",
    "enable_exact_asin": "ASIN önceliği",
}


def render_feature_chips(flags: dict, live_suggestions: bool):
    """Arama kutusunun altında aktif özellikleri chip olarak gösterir."""
    chips = []
    for key, label in _CHIP_LABELS.items():
        if flags.get(key):
            chips.append(f'<span class="chip chip-on">{label}</span>')
    if live_suggestions:
        chips.append('<span class="chip chip-on">Live suggestions</span>')
    if not chips:
        chips.append('<span class="chip">Aktif yöntem yok</span>')
    st.markdown(
        f'<div class="chip-row">{"".join(chips)}</div>', unsafe_allow_html=True
    )


def render_hero():
    """Üst hero/search bölümü başlığı."""
    st.markdown(
        """
        <div class="hero">
            <div class="hero-logo">🛍️</div>
            <div class="hero-title">Milyonlarca ürün içinde aradığını bul</div>
            <div class="hero-subtitle">
                Yazım hatalarına toleranslı, çok alanlı Elasticsearch ürün araması
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_result_header(query_text, total, shown, flags, intent_name):
    """Sonuçların üzerinde sorgu, toplam/gösterilen sayı, yöntemler ve intent."""
    e_query = html.escape(query_text)
    methods = ", ".join(
        _METHOD_LABELS_TR[k] for k, v in flags.items() if v
    ) or "yok"
    intent_html = ""
    if intent_name == "watch":
        intent_html = (
            '<div class="intent-badge">🕒 Saat kategorisi niyeti algılandı</div>'
        )
    st.markdown(
        f"""
        <div class="result-header">
            <div class="rh-query">"{e_query}" için {total:,} sonuç</div>
            <div class="rh-meta">İlk {shown} sonuç gösteriliyor · Aktif yöntemler: {methods}</div>
            {intent_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# Boş ekranda gösterilecek örnek sorgular.
_EXAMPLE_QUERIES = ["wireless headphones", "air filter", "smartwatch", "coffee grinder"]


def _select_query(new_value: str):
    """Bir başlık/örnek seçildiğinde inputu doldurup normal aramayı tetikler."""
    st.session_state["pending_value"] = new_value
    st.session_state["query_widget_version"] = (
        st.session_state.get("query_widget_version", 0) + 1
    )
    st.session_state["run_search"] = True
    st.session_state["hide_suggestions_once"] = True


def render_empty_state():
    """Arama yapılmadan önce gösterilen canlı boş ekran + örnek sorgu chip'leri."""
    st.markdown(
        """
        <div class="empty-state">
            <div class="es-icon">🔍</div>
            <div class="es-text">Bir ürün adı yazın ya da örneklerden birini deneyin</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    cols = st.columns(len(_EXAMPLE_QUERIES))
    for col, example in zip(cols, _EXAMPLE_QUERIES):
        with col:
            if st.button(example, key=f"ex_{example}", use_container_width=True):
                _select_query(example)
                st.rerun()


def _make_search_input(widget_key: str, default_val: str) -> str:
    """
    Arama kutusunu oluşturur. st_keyup varsa gerçek tuş-bazlı (350ms debounce)
    input; yoksa düz st.text_input (Enter/odak ile) kullanılır.
    """
    if HAS_KEYUP:
        return st_keyup(
            "Ürün ara",
            value=default_val,
            debounce=350,
            key=widget_key,
            label_visibility="collapsed",
            placeholder="Örn: wireless headphones",
        )

    def _cb():
        st.session_state["run_search"] = True

    return st.text_input(
        "Ürün ara",
        value=default_val,
        key=widget_key,
        label_visibility="collapsed",
        placeholder="Örn: wireless headphones",
        on_change=_cb,
    )


# ---------------------------------------------------------------------------
# Arayüz
# ---------------------------------------------------------------------------
def main():
    render_hero()

    config_error = check_configuration()
    if config_error:
        st.error(config_error)
        return

    flags, live_suggestions = render_search_settings()
    any_method_on = any(flags.values())
    settings_sig = tuple(sorted(flags.items()))

    # Session defaults
    st.session_state.setdefault("query_widget_version", 0)
    st.session_state.setdefault("run_search", False)

    # Arama kutusu + Ara butonu. Widget key versiyonlanır ki öneri/örnek seçimi
    # inputu güvenle güncelleyebilsin (DuplicateWidgetID / state döngüsü olmadan).
    version = st.session_state["query_widget_version"]
    widget_key = f"search_box_v{version}"
    default_val = st.session_state.get("pending_value", "")

    col_input, col_button = st.columns([5, 1])
    with col_input:
        typed = _make_search_input(widget_key, default_val)
    with col_button:
        if st.button("Ara", type="primary", use_container_width=True):
            st.session_state["run_search"] = True
    # pending_value yalnızca yeni widget'ın default'u olarak bir kez kullanılır.
    st.session_state.pop("pending_value", None)

    query_text = (typed or "").strip()
    st.session_state["current_query"] = query_text

    render_feature_chips(flags, live_suggestions)

    # -----------------------------------------------------------------------
    # Canlı öneriler — gerçek Edge NGram test indexi (title.autocomplete).
    # Lexical arama switchlerinden BAĞIMSIZDIR; tüm lexical yöntemler kapalı
    # olsa bile "Canlı öneriler" açıksa ve sorgu >= 3 karakterse çalışır.
    # st_keyup ile input değeri ~350ms debounce sonrası backend'e ulaştığında
    # ilk 5 benzersiz öneri gösterilir. watch-intent boost/dışlama korunur.
    # -----------------------------------------------------------------------
    show_suggestions = live_suggestions and not st.session_state.pop(
        "hide_suggestions_once", False
    )
    if show_suggestions:
        if query_text and len(query_text) < 3:
            st.caption("Öneriler için en az 3 karakter yazın.")
        elif len(query_text) >= 3:
            suggestions, sug_error = get_suggestions(query_text)
            if sug_error:
                st.warning("Canlı öneriler şu anda alınamadı.")
            elif suggestions:
                st.markdown(
                    '<div class="suggest-panel-title">En alakalı öneriler</div>',
                    unsafe_allow_html=True,
                )
                for idx, item in enumerate(suggestions):
                    if render_suggestion_row(item, idx):
                        # Öneri seçildi: inputu doldur, normal aramayı çalıştır,
                        # önerileri kapat. Amazon otomatik açılmaz.
                        _select_query(item["title"])
                        st.rerun()
                st.caption(
                    "Skor, Elasticsearch relevance değeridir; olasılık yüzdesi değildir."
                )

    # -----------------------------------------------------------------------
    # Normal arama tetikleyicisi
    # -----------------------------------------------------------------------
    triggered = st.session_state.get("run_search", False)

    if triggered:
        st.session_state["run_search"] = False  # tek seferlik tüket
        if not query_text:
            st.warning("Lütfen aramadan önce bir ürün adı yazın.")
        elif not any_method_on:
            # Normal arama için en az bir lexical yöntem gerekir; öneriler
            # yukarıda bağımsız çalışmaya devam eder.
            st.warning("Normal arama için en az bir arama yöntemi açık olmalı.")
        else:
            with st.spinner("Ürünler aranıyor..."):
                hits, total, error = search_products(query_text, **flags)
            # Sonuçları sakla (rerun'larda kaybolmasın); ayar imzasını da tut.
            st.session_state["search_query"] = query_text
            st.session_state["search_hits"] = hits
            st.session_state["search_total"] = total
            st.session_state["search_error"] = error
            st.session_state["search_sig"] = settings_sig
            if not error and hits:
                st.session_state["es_ok"] = True

    # -----------------------------------------------------------------------
    # Sonuç gösterimi (saklı sonuçlardan). Ayarlar değiştiyse eski ayarların
    # sonucunu yeni ayarlar altında sessizce gösterme.
    # -----------------------------------------------------------------------
    has_stored = "search_hits" in st.session_state
    if has_stored and st.session_state.get("search_sig") != settings_sig:
        # Ayarlar değişti: bayat sonuçları temizle.
        for k in ("search_hits", "search_total", "search_error",
                  "search_query", "search_sig"):
            st.session_state.pop(k, None)
        has_stored = False
        st.info("Arama ayarları değişti. Sonuçları güncellemek için tekrar arayın.")

    if not has_stored:
        if not query_text:
            render_empty_state()
        return

    error = st.session_state.get("search_error")
    if error:
        st.error(error)
        return

    hits = st.session_state.get("search_hits") or []
    total = st.session_state.get("search_total", 0)
    stored_query = st.session_state.get("search_query", query_text)

    if not hits:
        st.info(f'"{stored_query}" için sonuç bulunamadı. Farklı bir arama deneyin.')
        return

    intent_name = detect_search_intent(stored_query).get("intent")
    render_result_header(stored_query, total, len(hits), flags, intent_name)

    for hit in hits:
        render_product_card(hit)


if __name__ == "__main__":
    main()