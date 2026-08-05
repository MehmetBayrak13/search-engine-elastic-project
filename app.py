"""
Ürün Arama — Elastic Cloud destekli, tıklanabilir kartlı e-ticaret arama arayüzü.

Çalıştırma:
    pip install -r requirements.txt

    # Linux / macOS:
    #   export ELASTICSEARCH_URL="https://<deployment>.es.<region>.cloud.es.io"
    #   export ELASTICSEARCH_API_KEY="<api_key>"
    # Windows (PowerShell):
    #   $env:ELASTICSEARCH_URL="https://<deployment>.es.<region>.cloud.es.io"
    #   $env:ELASTICSEARCH_API_KEY="<api_key>"

    streamlit run app.py

Arama davranışı (index adları, boostlar, limitler, UI metinleri, intent
kuralları, çeviri sözlüğü) `config/` altındaki JSON dosyalarından ve
`config.py`'den okunur. Sırlar (URL/API key) yalnızca ortam değişkenlerinden
gelir; hiçbir zaman config dosyalarına yazılmaz.

Mimari: bu dosya artık yalnızca UI render'ı ve Streamlit session_state
orkestrasyonu yapar. Elasticsearch sorgu oluşturma/çalıştırma mantığı
`services/search_service.py` ve `services/autocomplete_service.py`da yaşar
(Streamlit'e bağımlı değildir, ileride bir FastAPI endpoint'i de aynı
servisleri kullanabilir). Arama kutusu + canlı öneri dropdown'u
`components/search_input/` altındaki bağımsız bir Streamlit custom
component'idir (bkz. o paketin CONTRACT.md'si) — component hiçbir arama iş
mantığı içermez, yalnızca generic `{type, query, event_id}` olayları emit eder.
"""

import html
import uuid

import streamlit as st

from components.search_input import search_input
from services import autocomplete_service, search_service

# ---------------------------------------------------------------------------
# Yapılandırma — gerçek yükleme services/search_service.py'de yapılır; burada
# yalnızca UI render'ı için kullanılan bir anlık görüntüsü (snapshot) alınır.
# Üretimde CONFIG hiçbir zaman runtime'da değişmediğinden bu güvenlidir;
# testler sorgu oluşturma davranışını değiştirmek istediğinde
# `search_service.CONFIG`ı (bu modüldeki `CONFIG` değil) mutate eder — bkz.
# tests/test_pagination.py, tests/test_quality_ranking.py.
# ---------------------------------------------------------------------------
CONFIG = search_service.CONFIG
CONFIG_ERROR = search_service.CONFIG_ERROR
INTENT_RULES = search_service.INTENT_RULES
INDEX_NAME = search_service.INDEX_NAME
AUTOCOMPLETE_INDEX_NAME = autocomplete_service.AUTOCOMPLETE_INDEX_NAME
PLACEHOLDER_IMAGE = CONFIG.ui.placeholder_image if CONFIG else ""

# Test/geri-uyum kolaylığı için services katmanının üst seviye
# fonksiyonlarını bu modülün isim alanına da açıyoruz (ör. `app.build_search_query(...)`
# doğrudan çalışır). `main()`'in GERÇEK çağrıları ise önbellekli fetcher'ları
# enjekte etmek için `search_service.X(...)`/`autocomplete_service.X(...)`
# şeklinde MODÜL REFERANSIYLA yapılır (bkz. aşağıdaki `_fetch_*` sarmalayıcılar).
from services.search_models import PaginationLimitError, SearchResult  # noqa: E402
from services.search_service import (  # noqa: E402
    build_category_discovery_query,
    build_search_query,
    detect_search_intent,
    discover_category_intent,
    expand_multilingual_query,
    resolve_intent_signals,
    search_products,
)
from services.autocomplete_service import build_autocomplete_query, get_suggestions  # noqa: E402

# st.cache_data'nın ttl parametresi decorator uygulanırken (import zamanında)
# değerlendirilir; bu yüzden CONFIG yüklendikten hemen sonra modül seviyesinde
# sabitlere okunur.
_DISCOVERY_CACHE_TTL = CONFIG.dynamic_intent.cache_ttl_seconds if CONFIG else 300
_AUTOCOMPLETE_CACHE_TTL = CONFIG.limits.autocomplete_cache_ttl_seconds if CONFIG else 30

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
    """Gerekli ortam değişkenlerini ve config dosyalarını kontrol eder;
    eksik/geçersizse hata mesajı döner."""
    if CONFIG_ERROR:
        if CONFIG is not None:
            return CONFIG.ui.message("config_load_error", error=CONFIG_ERROR)
        return f"Yapılandırma yüklenemedi: {CONFIG_ERROR}"

    missing = []
    if not search_service.ES_URL:
        missing.append("ELASTICSEARCH_URL")
    if not search_service.ES_API_KEY:
        missing.append("ELASTICSEARCH_API_KEY")

    if missing:
        return CONFIG.ui.message("config_missing_env", missing=", ".join(missing))
    return None


# ---------------------------------------------------------------------------
# Elasticsearch önbellekleme (Streamlit'e özgü — bu yüzden services/ değil
# burada yaşar). `search_service`/`autocomplete_service`deki önbelleksiz
# fetch fonksiyonlarını sarmalar; `main()` bunları `fetch_aggregations`/
# `fetch_hits` DI parametreleri olarak enjekte eder. Fonksiyon adları ve
# `.clear()` davranışı eskisiyle birebir aynıdır (bkz. tests/test_dynamic_category_discovery.py).
# ---------------------------------------------------------------------------
@st.cache_data(ttl=_DISCOVERY_CACHE_TTL, show_spinner=False)
def _fetch_category_aggregations(query_text: str, extra_query_texts: tuple[str, ...]):
    return search_service.fetch_category_aggregations(query_text, extra_query_texts)


@st.cache_data(ttl=_AUTOCOMPLETE_CACHE_TTL, show_spinner=False)
def _fetch_suggestion_hits(query_text: str, result_size: int):
    return autocomplete_service.fetch_suggestion_hits(query_text, result_size)


# ---------------------------------------------------------------------------
# Tıklanabilir kart (st.iframe ile — onclick/onerror JS'i çalıştırmak için
# iframe izolasyonu gerekiyor, st.html varsayılan olarak JS'i çalıştırmaz)
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
    product_url = CONFIG.product_url_template.format(asin=asin) if asin else ""

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
        /* İçerik satır sayısı aşağıdaki .product-title/.product-meta/.product-rating
           kurallarıyla sabit satır yüksekliklerine kısıtlanıyor (bkz. card_height
           hesaplaması); overflow:hidden burada gerçek taşmayı gizlemek için değil,
           kısıtlamanın garanti altına alınması için bir güvenlik önlemi. */
        html, body {{ overflow: hidden; }}
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
        /* Başlık düz koyu metin — link değil, alt çizgi/mavi renk YOK.
           Amazon başlıkları çoğu zaman çok uzun (100+ karakter); satır sayısı
           2 ile sınırlanmazsa kart, sabit iframe yüksekliğini taşırıp dikey
           scrollbar'a yol açıyordu (asıl kök neden). line-height px cinsinden
           sabitlenip card_height hesaplamasıyla birebir eşleşiyor. */
        .product-title {{
            font-size: 1.05rem;
            font-weight: 600;
            color: #111827;
            margin-bottom: 8px;
            line-height: 22px;
            max-height: 44px;
            text-decoration: none;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
            word-break: break-word;
        }}
        .product-meta {{
            color: #6b7280;
            font-size: 0.88rem;
            line-height: 18px;
            margin-bottom: 4px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .product-rating {{
            color: #374151;
            font-size: 0.9rem;
            line-height: 18px;
            margin-top: 6px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .product-price {{
            color: #059669;
            font-weight: 600;
            font-size: 0.95rem;
            line-height: 18px;
            margin-top: 4px;
        }}
        .score-badge {{
            display: inline-block;
            background: #f3f4f6;
            color: #4b5563;
            font-size: 0.78rem;
            line-height: 16px;
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

    # Kart yüksekliği CSS'teki sabit line-height'larla birebir hesaplanıyor
    # (bkz. .product-title/.product-meta/.product-rating/.score-badge):
    #   padding (18*2)                       = 36
    #   başlık (2 satır * 22px + 8px margin) = 52
    #   mağaza + kategori (2 * (18+4)px)     = 44
    #   değerlendirme (18 + 6px margin)      = 24
    #   skor rozeti (16 + 4 padding + 8 margin) = 28
    #   ---------------------------------------------
    #   ara toplam                            = 184
    #   + fiyat satırı (varsa, 18 + 4px margin) = 22
    #   + tarayıcı/font metriği toleransı      = 16
    # card container yüksekliği ile st.iframe(height=...) birebir eşleşmeli;
    # aksi halde iframe kendi içinde dikey scrollbar gösterir.
    card_height = 224 if price_html else 200
    # st.iframe: kart onclick/onkeydown JS'i ve <script> bloğu içeriyor, bu yüzden
    # st.html değil, JS çalıştırabilen iframe izolasyonu (st.iframe) kullanılıyor.
    st.iframe(card_html, height=card_height, width="stretch")


# ---------------------------------------------------------------------------
# Autocomplete öneri paneli — search_input component'inin `suggestions`
# prop'una geçilecek JSON-safe, HTML-kaçışlı payload'ı üretir. Component'in
# kendisi hiçbir kaçışlama yapmaz (bkz. components/search_input/CONTRACT.md).
# ---------------------------------------------------------------------------
def _build_suggestion_payload(suggestions: list) -> list[dict]:
    """`SuggestionItem` listesini (bkz. services.search_models) component'in
    beklediği `{title_html, title_text, meta_html, image_url, asin}`
    sözlüklerine çevirir. `image_url`, component'te `<img>.src` property'sine
    doğrudan atanır (innerHTML DEĞİL) — bu yüzden html.escape'e gerek yok/
    yanlış olur (URL'i bozar); `title_html`/`meta_html` ise innerHTML ile
    basıldığından kaçışlanır."""
    payload = []
    for item in suggestions:
        title = item.title or "Ürün adı bulunamadı"
        meta_parts = []
        if item.store:
            meta_parts.append(f"🏬 {html.escape(str(item.store))}")
        if item.average_rating is not None:
            meta_parts.append(f"⭐ {html.escape(str(item.average_rating))}")
        try:
            if item.price is not None and float(item.price) > 0:
                meta_parts.append(f"💲 {float(item.price):.2f}")
        except (TypeError, ValueError):
            pass

        payload.append({
            "title_html": html.escape(str(title)),
            "title_text": title,
            "meta_html": " · ".join(meta_parts) if meta_parts else html.escape("Detay yok"),
            "image_url": str(item.image_url) if item.image_url else PLACEHOLDER_IMAGE,
            "asin": item.asin or "",
        })
    return payload


def _handle_search_input_event(event: dict | None) -> tuple[str, bool]:
    """
    `search_input(...)` component'inin döndürdüğü son olayı işler.

    Aynı `event_id` tekrar görülürse (ör. Ara butonuna basmak veya sidebar
    switch'lerini değiştirmek gibi component'le İLGİSİZ bir rerun) hiçbir şey
    tekrar TETİKLENMEZ — yalnızca son bilinen sorgu metni döner. Bu, bir
    "submit"/"select" olayının yanlışlıkla birden fazla kez işlenmesini
    (ör. sayfa 6'dayken tekrar tetiklenip current_page'in beklenmedik şekilde
    değişmesini) engeller.

    "submit" (Enter, panelde aktif öneri YOKKEN) ve "select" (bir öneriye
    tıklama VEYA panelde aktif öneri varken Enter) AYNI `_trigger_explicit_search`/
    `_select_query` state geçişini kullanır — Ara butonuyla TAMAMEN AYNI kod
    yolu. "typing" hiçbir arama TETİKLEMEZ; yalnızca canlı öneri panelinin
    güncellenmesi için güncel sorgu metnini bildirir.

    Dönüş: (güncel_sorgu_metni, bu_run'da_yeni_bir_olay_işlendi_mi)
    """
    if not event:
        return st.session_state.get("current_query", ""), False

    query = event.get("query") or ""
    last_id = st.session_state.get("_search_input_last_event_id")
    if event.get("event_id") == last_id:
        return query, False

    st.session_state["_search_input_last_event_id"] = event.get("event_id")
    event_type = event.get("type")
    if event_type == "submit":
        _trigger_explicit_search()
    elif event_type == "select":
        _select_query(query)
    # "typing": tetikleyici yok, yalnızca query metni güncellenir.
    return query, True


def _suggestions_feedback_changed(
    old_payload: list, new_payload: list, old_error: bool, new_error: bool
) -> bool:
    """Yeni fetch edilen öneri/hatanın component'e geri beslenmek için
    GERÇEKTEN farklı olup olmadığını söyler.

    Bir "typing" olayı işlendiğinde öneriler her zaman component'e aynı run
    içinde geri verilemez (bkz. main() içindeki ilgili yorum) — bu yüzden bir
    ek `st.rerun()` gerekir, ANCAK yalnızca sonuç önceki durumdan farklıysa.
    Aksi halde (ör. art arda gelen prefix'ler aynı üst-N sonucu döndürdüğünde)
    hiçbir görsel değişikliği olmayan bir rerun sayfayı gereksiz yere
    yeniden çizer — art arda tetiklenince bu, kullanıcı yazarken sürekli bir
    "titreme" (flicker) ve input'un adeta kullanılamaz hale gelmesi izlenimi
    yaratır. Panel/hata durumu değişmediyse bir sonraki normal render zaten
    aynı `stored_suggestions`'ı kullanacağından ek rerun'a gerek yoktur.
    """
    return new_payload != old_payload or new_error != old_error


# ---------------------------------------------------------------------------
# Arayüz yardımcıları
# ---------------------------------------------------------------------------
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
    ui = CONFIG.ui
    st.sidebar.markdown(f"### {ui.label('settings_panel_title', 'Arama Ayarları')}")
    enable_phrase = _toggle(
        ui.label("toggle_phrase"), True, "opt_phrase", ui.help("toggle_phrase"),
    )
    enable_multi = _toggle(
        ui.label("toggle_multi_match"), True, "opt_multi", ui.help("toggle_multi_match"),
    )
    enable_fuzzy = _toggle(
        ui.label("toggle_fuzzy"), True, "opt_fuzzy", ui.help("toggle_fuzzy"),
    )
    enable_asin = _toggle(
        ui.label("toggle_exact_asin"), True, "opt_asin", ui.help("toggle_exact_asin"),
    )
    st.sidebar.markdown("---")
    live_suggestions = _toggle(
        ui.label("toggle_live_suggestions"), True, "opt_suggest", ui.help("toggle_live_suggestions"),
    )

    flags = {
        "enable_phrase": enable_phrase,
        "enable_multi_match": enable_multi,
        "enable_fuzzy": enable_fuzzy,
        "enable_exact_asin": enable_asin,
    }

    # Sistem durumu (gerçek bağlantı doğrulanmadan "Bağlı" denmez).
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"#### {ui.label('system_status_title', 'Sistem Durumu')}")
    status_key = "status_connected" if st.session_state.get("es_ok") else "status_ready"
    es_state = ui.label(status_key)
    st.sidebar.caption(
        ui.message(
            "system_status_caption",
            status=es_state,
            index_count=len(CONFIG.elasticsearch.search_indices),
            result_size=CONFIG.limits.result_size,
            suggestion_limit=CONFIG.limits.autocomplete_display_size,
        )
    )
    return flags, live_suggestions


# Sidebar switch anahtarları ile config'teki chip/method etiket anahtarlarının eşleşmesi.
_FLAG_CHIP_KEYS = {
    "enable_phrase": "chip_phrase",
    "enable_multi_match": "chip_multi_match",
    "enable_fuzzy": "chip_fuzzy",
    "enable_exact_asin": "chip_exact_asin",
}
_FLAG_METHOD_KEYS = {
    "enable_phrase": "method_phrase",
    "enable_multi_match": "method_multi_match",
    "enable_fuzzy": "method_fuzzy",
    "enable_exact_asin": "method_exact_asin",
}


def render_feature_chips(flags: dict, live_suggestions: bool):
    """Arama kutusunun altında aktif özellikleri chip olarak gösterir."""
    ui = CONFIG.ui
    chips = []
    for key, chip_key in _FLAG_CHIP_KEYS.items():
        if flags.get(key):
            chips.append(f'<span class="chip chip-on">{ui.label(chip_key)}</span>')
    if live_suggestions:
        chips.append(f'<span class="chip chip-on">{ui.label("chip_live_suggestions")}</span>')
    if not chips:
        chips.append(f'<span class="chip">{ui.label("chip_none", "Aktif yöntem yok")}</span>')
    st.markdown(
        f'<div class="chip-row">{"".join(chips)}</div>', unsafe_allow_html=True
    )


def render_hero():
    """Üst hero/search bölümü başlığı."""
    ui = CONFIG.ui
    st.markdown(
        f"""
        <div class="hero">
            <div class="hero-logo">{ui.hero_logo}</div>
            <div class="hero-title">{html.escape(ui.hero_title)}</div>
            <div class="hero-subtitle">
                {html.escape(ui.hero_subtitle)}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


_RESULTS_TOP_ANCHOR_ID = "search-results-top-anchor"


def render_result_header(query_text, total, shown, flags, intent_name):
    """Sonuçların üzerinde sorgu, toplam/gösterilen sayı, yöntemler ve intent.

    Ayrıca `_RESULTS_TOP_ANCHOR_ID` kimlikli görünmez bir anchor div çizer;
    sayfa değişiminde `_scroll_results_to_top()` viewport'u bu anchor'a
    kaydırmayı dener (bkz. o fonksiyonun docstring'i).
    """
    ui = CONFIG.ui
    e_query = html.escape(query_text)
    methods = ", ".join(
        ui.label(_FLAG_METHOD_KEYS[k]) for k, v in flags.items() if v
    ) or "yok"

    intent_html = ""
    if intent_name and intent_name in INTENT_RULES:
        rule = INTENT_RULES[intent_name]
        icon = rule.icon or ui.intent_fallback_icon
        intent_html = f'<div class="intent-badge">{icon} {html.escape(rule.label)}</div>'

    query_line = ui.message("result_header_query", query=e_query, total=total)
    meta_line = ui.message("result_header_meta", shown=shown, methods=methods)

    st.markdown(
        f"""
        <div id="{_RESULTS_TOP_ANCHOR_ID}"></div>
        <div class="result-header">
            <div class="rh-query">{query_line}</div>
            <div class="rh-meta">{meta_line}</div>
            {intent_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_pagination_bar(position: str):
    """
    Sayfalama bilgisini (aralık + sayfa göstergesi) ve Önceki/Sonraki
    butonlarını çizer. Yalnızca saklı arama sonucu metadata'sını
    (`st.session_state`, `search_products` tarafından doldurulur) okur —
    kendi başına arama tetiklemez. `main()` bu fonksiyonu artık yalnızca
    sonuçların ALTINDA (`position="bottom"`) çağırır; `position` parametresi
    Streamlit widget key'lerini benzersizleştirmek için ve fonksiyonun tek
    başına (örn. testlerde) farklı konumlarla çağrılabilmesi için duruyor.

    Pagination devre dışıysa veya tek sayfa varsa hiçbir şey çizmez.
    """
    if not CONFIG.pagination.enabled:
        return
    total_pages = st.session_state.get("search_total_pages", 0)
    if total_pages <= 1:
        return

    ui = CONFIG.ui
    current_page = st.session_state.get("current_page", 1)
    start_item = st.session_state.get("search_start_item", 0)
    end_item = st.session_state.get("search_end_item", 0)
    has_previous = st.session_state.get("search_has_previous", False)
    has_next = st.session_state.get("search_has_next", False)

    st.caption(ui.message("pagination_range_indicator", start=start_item, end=end_item))
    st.caption(
        ui.message("pagination_page_indicator", current_page=current_page, total_pages=total_pages)
    )

    col_prev, col_next = st.columns(2)
    with col_prev:
        if st.button(
            ui.label("pagination_prev_button", "← Önceki"),
            key=f"pg_prev_{position}",
            disabled=not has_previous,
            use_container_width=True,
        ):
            st.session_state["current_page"] = _previous_page(current_page)
            st.session_state["run_search"] = True
            st.session_state["scroll_to_top_once"] = True
            st.rerun()
    with col_next:
        if st.button(
            ui.label("pagination_next_button", "Sonraki →"),
            key=f"pg_next_{position}",
            disabled=not has_next,
            use_container_width=True,
        ):
            st.session_state["current_page"] = _next_page(current_page)
            st.session_state["run_search"] = True
            st.session_state["scroll_to_top_once"] = True
            st.rerun()

    total = st.session_state.get("search_total", 0)
    if total > CONFIG.pagination.max_result_window:
        st.caption(
            ui.message("pagination_window_notice", max_result_window=CONFIG.pagination.max_result_window)
        )


def _scroll_results_to_top():
    """
    Sayfa değişince (Önceki/Sonraki) viewport'u `render_result_header`'ın
    çizdiği `#_RESULTS_TOP_ANCHOR_ID` anchor'ına kaydırmayı best-effort dener.
    Görsel bir kolaylıktır; pagination'ın kendisi (from/size, state) buna
    bağımlı değildir.

    `st.iframe(..., height="content")` kullanılır — bkz. product card
    render'ındaki aynı iframe/JS izolasyon gerekçesi. Her çağrıda benzersiz
    bir `uuid4` nonce'u HTML yorumu olarak gövdeye eklenir; aksi halde
    ardışık sayfa geçişlerinde üretilen HTML bayt bayt aynı kalır ve
    Streamlit'in frontend'i iframe'in `srcdoc`'unu değişmemiş sayıp DOM
    düğümünü yeniden yüklemez — bu da içindeki `<script>`in yalnızca İLK
    sayfa geçişinde çalışmasına yol açar.

    `window.parent.document` erişimi başarısız olursa (ör. farklı origin)
    script bunu try/catch ile tespit edip sessizce vazgeçer. Component
    render'ı sırasında oluşabilecek HERHANGİ bir hata da burada yutulur —
    scroll kritik olmadığından bir tarayıcı/render hatası pagination akışını
    asla kesmemeli.
    """
    try:
        nonce = uuid.uuid4().hex
        scroll_html = f"""
        <html>
        <head><style>html, body {{ margin: 0; padding: 0; overflow: hidden; }}</style></head>
        <body>
        <!-- nonce:{nonce} — srcdoc'u her çağrıda farklılaştırıp yeniden
             yüklemeyi zorlamak için; içerik olarak bir anlamı yok. -->
        <script>
        (function() {{
            var MAX_ATTEMPTS = 30;
            var attempts = 0;

            function tryScroll() {{
                attempts += 1;
                var parentDoc;
                try {{
                    parentDoc = window.parent && window.parent.document;
                }} catch (err) {{
                    // window.parent erişimi engellenmiş (ör. farklı origin) —
                    // sessizce vazgeç, pagination'ı etkileme.
                    return;
                }}
                if (!parentDoc) {{
                    return;
                }}
                var anchor = parentDoc.getElementById("{_RESULTS_TOP_ANCHOR_ID}");
                if (anchor) {{
                    anchor.scrollIntoView({{behavior: "smooth", block: "start"}});
                    return;
                }}
                if (attempts < MAX_ATTEMPTS) {{
                    window.requestAnimationFrame(tryScroll);
                }}
            }}

            try {{
                window.requestAnimationFrame(tryScroll);
            }} catch (err) {{
                // Best-effort: tarayıcı tarafında scroll başarısız olursa
                // sessizce yut, pagination'ı etkilemesin.
            }}
        }})();
        </script>
        </body>
        </html>
        """
        st.iframe(scroll_html, height="content", width="stretch")
    except Exception:
        # Component render'ı başarısız olsa bile arama sonuçları normal
        # şekilde gösterilmeye devam etmeli.
        return


def _trigger_explicit_search():
    """
    "Explicit search" tetikleyicilerinin (Ara butonu, Enter/submit olayı,
    öneri seçimi, örnek sorgu seçimi) PAYLAŞTIĞI tek state geçişi.

    Her explicit search, `current_page`i normal arama akışı ÇALIŞMADAN ÖNCE
    1'e sıfırlar. Böylece `_resolve_page_for_new_search` aynı sorgu için
    bile zaten 1 olan `requested_page`i normalize edip 1 döner —
    fonksiyonun kendi "aynı sorguda sayfayı koru" davranışına dokunulmadan
    (Önceki/Sonraki hâlâ doğru çalışır) sonuç doğru olur.
    """
    st.session_state["current_page"] = 1
    st.session_state["run_search"] = True


def _select_query(new_value: str):
    """Bir başlık/örnek seçildiğinde inputu doldurup normal aramayı tetikler."""
    st.session_state["pending_value"] = new_value
    st.session_state["query_widget_version"] = (
        st.session_state.get("query_widget_version", 0) + 1
    )
    st.session_state["hide_suggestions_once"] = True
    st.session_state["_suggestions_payload"] = []
    _trigger_explicit_search()


def _resolve_page_for_new_search(
    previous_query: str | None, new_query: str, requested_page: int
) -> int:
    """
    Bir arama tetiklendiğinde hangi sayfanın isteneceğine karar veren saf
    fonksiyon (Elasticsearch'e istek atmaz, Streamlit'e bağımlı değildir).

    Sorgu metni bir öncekinden FARKLIYSA (yeni yazılan sorgu, öneri seçimi,
    örnek sorgu seçimi — hepsi query_text'i değiştirir) her zaman sayfa 1'e
    döner. Aynı sorguyla tekrar arama (Önceki/Sonraki butonları query_text'i
    DEĞİŞTİRMEZ) `requested_page`i korur; yalnızca `requested_page < 1` ise
    1'e normalize edilir.
    """
    if new_query != previous_query:
        return 1
    return search_service._normalize_page(requested_page)


def _next_page(current_page: int) -> int:
    return current_page + 1


def _previous_page(current_page: int) -> int:
    return max(1, current_page - 1)


def _settings_changed(previous_sig: tuple | None, current_sig: tuple) -> bool:
    """Sidebar arama yöntemi switch'lerinin bir önceki aramadan beri
    değişip değişmediğini söyler; değiştiyse saklı sonuçlar bayat sayılır ve
    sayfa 1'e sıfırlanır (bkz. main())."""
    return previous_sig != current_sig


def render_empty_state():
    """Arama yapılmadan önce gösterilen canlı boş ekran + örnek sorgu chip'leri."""
    ui = CONFIG.ui
    st.markdown(
        f"""
        <div class="empty-state">
            <div class="es-icon">{ui.label('empty_state_icon', '🔍')}</div>
            <div class="es-text">{html.escape(ui.label('empty_state_text'))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    example_queries = CONFIG.ui.example_queries
    cols = st.columns(len(example_queries))
    for col, example in zip(cols, example_queries):
        with col:
            if st.button(example, key=f"ex_{example}", use_container_width=True):
                _select_query(example)
                st.rerun()


# ---------------------------------------------------------------------------
# Arayüz
# ---------------------------------------------------------------------------
def main():
    render_hero()

    config_error = check_configuration()
    if config_error:
        st.error(config_error)
        return

    ui = CONFIG.ui
    flags, live_suggestions = render_search_settings()
    any_method_on = any(flags.values())
    settings_sig = tuple(sorted(flags.items()))

    # Session defaults
    st.session_state.setdefault("query_widget_version", 0)
    st.session_state.setdefault("run_search", False)
    st.session_state.setdefault("current_page", 1)

    # Arama kutusu + Ara butonu. Widget key versiyonlanır ki öneri/örnek seçimi
    # inputu güvenle güncelleyebilsin (DuplicateWidgetID / state döngüsü olmadan).
    version = st.session_state["query_widget_version"]
    widget_key = f"search_box_v{version}"
    default_val = st.session_state.get("pending_value", "")

    search_button_label = ui.label("search_button", "Ara")

    stored_suggestions = st.session_state.get("_suggestions_payload", [])
    stored_suggestions_error = st.session_state.get("_suggestions_error", False)
    col_input, col_button = st.columns([5, 1])
    with col_input:
        event = search_input(
            value=default_val,
            suggestions=stored_suggestions if live_suggestions else [],
            placeholder=CONFIG.ui.search_placeholder,
            debounce_ms=CONFIG.ui.debounce_ms,
            panel_max_height_px=CONFIG.autocomplete_ui.panel_max_height_px,
            row_height_px=CONFIG.autocomplete_ui.row_height_px,
            show_images=CONFIG.autocomplete_ui.show_images,
            key=widget_key,
        )
    with col_button:
        if st.button(search_button_label, type="primary", use_container_width=True):
            _trigger_explicit_search()
            # Component'in kendi "submit" olayı dropdown'u client-side kapatır;
            # Ara butonu component'i BYPASS ettiğinden aynı kapanmayı burada
            # session_state üzerinden garanti ederiz (bkz. bir sonraki rerun).
            st.session_state["_suggestions_payload"] = []

    # pending_value yalnızca YUKARIDAKİ `search_input(...)` çağrısının
    # default'u olarak bir kez kullanılır; burada hemen tüketilir. Bu POP,
    # `_handle_search_input_event`'TEN ÖNCE olmalı: aşağıdaki çağrı bir
    # "select" olayı işlerse `_select_query` YENİ bir pending_value set eder
    # (bir sonraki, bump'lanmış widget_key'in default'u için) — sıralama ters
    # olsaydı bu yeni değer, henüz kullanılmadan hemen silinirdi.
    st.session_state.pop("pending_value", None)
    typed, is_new_event = _handle_search_input_event(event)

    query_text = (typed or "").strip()
    st.session_state["current_query"] = query_text

    render_feature_chips(flags, live_suggestions)

    # -----------------------------------------------------------------------
    # Canlı öneriler — Edge NGram autocomplete indexi (title.autocomplete).
    # Lexical arama switchlerinden BAĞIMSIZDIR. `search_input` component'i
    # kendi debounce'unu (config'teki debounce_ms) client-side uygular; Python
    # tarafı yalnızca YENİ bir "typing" olayı geldiğinde öneri hesaplar.
    #
    # ÖNEMLİ: hesaplanan öneriler bu run'da component'e GERİ BESLENEMEZ (aynı
    # `key` ile aynı run içinde component'i ikinci kez çağırmak Streamlit'te
    # desteklenmez) — bu yüzden session_state'e yazılıp `st.rerun()` ile HEMEN
    # bir sonraki run'a taşınır; o run'da hiçbir yeni client-side olay
    # olmadığından aynı event_id geri döner (`is_new_event=False`, tekrar
    # tetiklenmez) ama component artık GÜNCEL önerileri props olarak alır.
    #
    # Bu ek rerun yalnızca `_suggestions_feedback_changed` GERÇEK bir fark
    # tespit ettiğinde yapılır (ör. hızlı art arda gelen prefix'ler aynı
    # üst-N öneriyi döndürdüğünde YAPILMAZ). Component'in kendi `event_id`'i
    # her "typing" olayında zaten benzersiz olduğundan (bkz. CONTRACT.md) her
    # tuş vuruşu tek başına Streamlit'in component-değeri-değişti otomatik
    # rerun'unu tetikler; buradaki KOŞULSUZ ikinci `st.rerun()` bunun üstüne
    # binip her tuş vuruşunda sayfayı iki kez tam olarak yeniden çizerdi —
    # gözlemlenen "sürekli yanıp sönme / input kullanılamaz hale gelme"
    # sorununun kök nedeni buydu. Koşullu hale getirmek, önerilerin hâlâ HER
    # ZAMAN en son yazılan metne ait olmasını (asıl garanti) korurken
    # gereksiz ikinci rerun'u ortadan kaldırır.
    # -----------------------------------------------------------------------
    min_chars = CONFIG.limits.autocomplete_min_chars
    show_suggestions = live_suggestions and not st.session_state.pop("hide_suggestions_once", False)

    if query_text and len(query_text) < min_chars and show_suggestions:
        st.caption(ui.message("suggestions_min_chars_caption", min_chars=min_chars))

    if is_new_event:
        event_type = (event or {}).get("type")
        if event_type == "typing" and show_suggestions and query_text and len(query_text) >= min_chars:
            suggestions, sug_error = autocomplete_service.get_suggestions(
                query_text, fetch_hits=_fetch_suggestion_hits
            )
            if sug_error:
                new_payload: list = []
                new_error = True
            else:
                new_payload = _build_suggestion_payload(suggestions)
                new_error = False
            st.session_state["_suggestions_payload"] = new_payload
            st.session_state["_suggestions_error"] = new_error
            if _suggestions_feedback_changed(
                stored_suggestions, new_payload, stored_suggestions_error, new_error
            ):
                st.rerun()
        elif event_type in ("typing", "submit", "select"):
            # Boş/kısa sorgu, öneriler kapalı veya submit/select: panel
            # kapanmalı — component bunu client-side zaten yapar, burada
            # yalnızca sonraki render'ın stale öneri göstermemesi sağlanır.
            st.session_state["_suggestions_payload"] = []
            if event_type == "select":
                # `_select_query` (yukarıda, `_handle_search_input_event`
                # içinde) `query_widget_version`'ı bump'ladı; YENİ widget_key
                # ancak BİR SONRAKİ run'da hesaplanır (bkz. main() başındaki
                # `widget_key` ataması) — aksi halde kutu bu run'da hâlâ ESKİ
                # component instance'ını gösterir. `run_search=True`
                # session_state'te kalıcı olduğundan arama bu rerun'dan SONRA
                # da doğru çalışır (bkz. örnek sorgu chip'lerindeki aynı desen).
                st.rerun()

    if st.session_state.pop("_suggestions_error", False):
        st.warning(ui.message("suggestions_unavailable_warning"))

    # -----------------------------------------------------------------------
    # Normal arama tetikleyicisi
    # -----------------------------------------------------------------------
    triggered = st.session_state.get("run_search", False)

    if triggered:
        st.session_state["run_search"] = False  # tek seferlik tüket
        if not query_text:
            st.warning(ui.message("empty_query_warning"))
        elif not any_method_on:
            # Normal arama için en az bir lexical yöntem gerekir; öneriler
            # yukarıda bağımsız çalışmaya devam eder.
            st.warning(ui.message("no_method_warning"))
        else:
            # Sayfa değişmeden (Önceki/Sonraki) tekrar arama isteği geldiyse
            # istenen sayfa korunur; sorgu metni değiştiyse (yeni yazım,
            # öneri/örnek seçimi) her zaman sayfa 1'e dönülür.
            previous_query = st.session_state.get("search_query")
            page_to_fetch = _resolve_page_for_new_search(
                previous_query, query_text, st.session_state.get("current_page", 1)
            )
            st.session_state["current_page"] = page_to_fetch

            with st.spinner("Ürünler aranıyor..."):
                result = search_service.search_products(
                    query_text, page=page_to_fetch,
                    fetch_aggregations=_fetch_category_aggregations, **flags)
            # Sonuçları sakla (rerun'larda kaybolmasın); ayar imzasını da tut.
            st.session_state["search_query"] = query_text
            st.session_state["search_hits"] = result.hits
            st.session_state["search_total"] = result.total
            st.session_state["search_error"] = result.error
            st.session_state["search_sig"] = settings_sig
            st.session_state["current_page"] = result.current_page
            st.session_state["search_page_size"] = result.page_size
            st.session_state["search_total_pages"] = result.total_pages
            st.session_state["search_start_item"] = result.start_item
            st.session_state["search_end_item"] = result.end_item
            st.session_state["search_has_previous"] = result.has_previous
            st.session_state["search_has_next"] = result.has_next
            if not result.error and result.hits:
                st.session_state["es_ok"] = True

    # -----------------------------------------------------------------------
    # Sonuç gösterimi (saklı sonuçlardan). Ayarlar değiştiyse eski ayarların
    # sonucunu yeni ayarlar altında sessizce gösterme.
    # -----------------------------------------------------------------------
    has_stored = "search_hits" in st.session_state
    if has_stored and _settings_changed(st.session_state.get("search_sig"), settings_sig):
        # Ayarlar değişti: bayat sonuçları temizle, sayfayı 1'e sıfırla.
        for k in ("search_hits", "search_total", "search_error",
                  "search_query", "search_sig", "search_page_size",
                  "search_total_pages", "search_start_item", "search_end_item",
                  "search_has_previous", "search_has_next"):
            st.session_state.pop(k, None)
        st.session_state["current_page"] = 1
        has_stored = False
        st.info(ui.message("settings_changed_info"))

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
        st.info(ui.message("no_results_info", query=stored_query))
        return

    intent_name = detect_search_intent(stored_query).get("intent")
    render_result_header(stored_query, total, len(hits), flags, intent_name)

    # Anchor, render_result_header'ın çizdiği #_RESULTS_TOP_ANCHOR_ID
    # div'i içeriyor; scroll tetiklemesi bu yüzden başlıktan SONRA gelir,
    # aksi halde anchor DOM'a henüz eklenmeden scriptin çalışması riske girer.
    if st.session_state.pop("scroll_to_top_once", False):
        _scroll_results_to_top()

    for hit in hits:
        render_product_card(hit)

    # Pagination kontrolü yalnızca sonuçların ALTINDA gösterilir — üstteki
    # pagination render çağrısı kasıtlı olarak yok.
    render_pagination_bar("bottom")


if __name__ == "__main__":
    main()
