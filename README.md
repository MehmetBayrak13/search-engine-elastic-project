# Amazon Elasticsearch Product Search

Elastic Cloud destekli ürün arama uygulaması. Amazon Reviews 2023 veri
setinden indexlenmiş ürünler üzerinde exact ASIN, phrase, multi-field ve
fuzzy arama; Edge NGram autocomplete; Elasticsearch aggregation tabanlı
dinamik kategori keşfi; opsiyonel manuel intent override katmanı; `from + size`
tabanlı sayfalama ve Türkçe→İngilizce sorgu genişletme destekler.

Uygulamanın **birincil** arayüzü artık ayrı bir **React frontend +
FastAPI backend** ikilisidir (`frontend/`, `api/`) — bkz. "React + FastAPI"
bölümü hemen aşağıda. Arama/autocomplete/intent/çeviri/kategori-keşif
mantığının TAMAMI `services/` katmanında yaşar ve Streamlit'e hiçbir zaman
bağımlı olmadığı için hem eski Streamlit arayüzü (`app.py`, hâlâ çalışır
durumda, referans/yedek olarak repoda kalır) hem de yeni FastAPI backend'i
BİREBİR AYNI kod yolunu kullanır — iki arayüz arasında arama davranışı
sapması yoktur.

## React + FastAPI (yeni — birincil arayüz)

```text
frontend/   → Vite + React tek sayfa uygulaması (arama kutusu + autocomplete
              dropdown, sidebar arama ayarları, ürün kartları, sayfalama,
              light/dark tema)
api/        → FastAPI backend: services/search_service.py ve
              services/autocomplete_service.py'yi HTTP üzerinden açar
              (/api/search, /api/autocomplete, /api/config, /api/health)
```

### Backend'i çalıştırma

```bash
pip install -r requirements-api.txt

# Linux / macOS
export ELASTICSEARCH_URL="https://<deployment>.es.<region>.cloud.es.io"
export ELASTICSEARCH_API_KEY="<api_key>"
export ALLOWED_ORIGINS="http://localhost:5173"   # frontend dev server origin(leri), virgülle ayrık

# Windows (PowerShell)
$env:ELASTICSEARCH_URL="https://<deployment>.es.<region>.cloud.es.io"
$env:ELASTICSEARCH_API_KEY="<api_key>"
$env:ALLOWED_ORIGINS="http://localhost:5173"

uvicorn api.main:app --reload --port 8000
```

`ELASTICSEARCH_URL`/`ELASTICSEARCH_API_KEY` yalnızca backend process'inde
okunur; frontend'e (tarayıcıya) hiçbir zaman gönderilmez — bkz. `api/main.py`
`_hit_to_product`/`get_config`, sır İÇERMEYEN alanları döner.

### Frontend'i çalıştırma

```bash
cd frontend
npm install
cp .env.example .env   # VITE_API_BASE_URL, backend adresine göre düzenlenebilir
npm run dev            # http://localhost:5173
```

Production build: `npm run build` (`frontend/dist/` — herhangi bir statik
dosya sunucusuna/CDN'e deploy edilebilir; bkz. aşağıdaki "Deploy" bölümü).

### API sözleşmesi

| Endpoint | Açıklama |
|---|---|
| `GET /api/health` | `{"ok": bool, "error": str\|null}` — eksik env değişkeni/config hatası |
| `GET /api/config` | Sır içermeyen UI config'i (labels, help_text, messages, hero, example_queries, debounce_ms, autocomplete_ui, limits, pagination) |
| `GET /api/search?q=&page=&enable_phrase=&enable_multi_match=&enable_fuzzy=&enable_exact_asin=` | `search_service.search_products` sonucu (hits her biri `product_url` dahil hazır JSON) |
| `GET /api/autocomplete?q=` | `autocomplete_service.get_suggestions` sonucu |

Önbellekleme: `api/cache.py`deki basit process-içi TTL cache, `app.py`deki
`st.cache_data(ttl=...)` sarmalayıcılarının doğrudan karşılığıdır (aynı TTL
değerleri, aynı config kaynağı).

### Deploy

Backend ve frontend AYRI süreçler/deploy hedefleridir (Streamlit Community
Cloud'un tek-tık deploy'unun yerini tutan tek bir hedef yoktur):

1. **Backend** (`api/`) — herhangi bir Python/ASGI hosting'ine
   (`uvicorn api.main:app`) deploy edilebilir. `ELASTICSEARCH_URL`,
   `ELASTICSEARCH_API_KEY`, `ALLOWED_ORIGINS` (frontend'in gerçek origin'i,
   örn. `https://mehmetbayrak.com`) ortam değişkeni olarak tanımlanmalı.
2. **Frontend** (`frontend/`) — `npm run build` çıktısı (`frontend/dist/`)
   herhangi bir statik dosya hosting'ine deploy edilebilir. Build ANINDA
   `VITE_API_BASE_URL`'in gerçek backend URL'ine işaret etmesi gerekir (Vite
   env değişkenlerini build-time'da gömer — deploy sağlayıcısının "environment
   variables" ayarına eklenmeli).
3. **Custom domain** (`mehmetbayrak.com`): domain satın alma, DNS kaydı
   oluşturma ve hosting sağlayıcısı hesabı açma bu ortamın dışında, yalnızca
   domain'in sahibi tarafından yapılabilecek adımlardır — bkz. proje kökündeki
   görev notları / son rapor.

Bu üç adım hiçbiri bu görev kapsamında otomatik yapılmadı; hiçbiri geri
alınamaz/ücretli bir işlem içermeyen "sadece kod" adımı değildir.

## Streamlit arayüzü (eski/referans, hâlâ çalışır)

Aşağıdaki "Kurulum ve çalıştırma" bölümünden itibaren anlatılanlar Streamlit
arayüzüne (`app.py`) aittir. Kaldırılmadı — `services/`/`config/` katmanı
paylaşıldığından bakımı ekstra yük getirmez ve React frontend'in davranışını
doğrulamak için referans olarak kullanılabilir.

## Kurulum ve çalıştırma

```bash
pip install -r requirements.txt
```

Elastic Cloud bağlantı bilgilerini ortam değişkeni olarak tanımlayın:

```bash
# Linux / macOS
export ELASTICSEARCH_URL="https://<deployment>.es.<region>.cloud.es.io"
export ELASTICSEARCH_API_KEY="<api_key>"

# Windows (PowerShell)
$env:ELASTICSEARCH_URL="https://<deployment>.es.<region>.cloud.es.io"
$env:ELASTICSEARCH_API_KEY="<api_key>"
```

Uygulamayı başlatın:

```bash
streamlit run app.py
```

## Testler

```bash
python -m py_compile app.py config.py services/search_models.py services/search_service.py services/autocomplete_service.py components/search_input/__init__.py
python -m pytest -q
```

## Yapılandırma

Sırlar (`ELASTICSEARCH_URL`, `ELASTICSEARCH_API_KEY`) her zaman ortam
değişkeninden veya Streamlit Secrets'tan gelir; asla dosyaya yazılmaz.

Arama davranışının tamamı `config/` altındaki JSON dosyalarından okunur —
`app.py` içinde index adı, boost değeri veya arayüz metni hardcoded değildir:

| Dosya | İçerik |
|---|---|
| `config/search_config.json` | Index adları, timeout/limit değerleri, exact ASIN / phrase / multi-match / fuzzy / autocomplete alan ve boost ayarları, çeviri ayarları, **dinamik kategori keşfi (`dynamic_intent`) ayarları**, **sayfalama (`pagination`) ayarları**, **autocomplete dropdown görsel ayarları (`autocomplete_ui`: panel yüksekliği, satır yüksekliği, görsel gösterme)**, dönen kaynak alanlar, arayüz metinleri |
| `config/intent_rules.json` | **Opsiyonel override katmanı** (örn. `watch`): alias/tetikleyici terimler, dışlama koşulları, force-boost terimleri, negatif kategoriler (exclusion), rozet metni/ikonu, `priority`. Boş `{}` da geçerlidir — hiç kural olmadan da çalışır; ana kategori-intent motoru bu dosya DEĞİL, dinamik kategori keşfidir (aşağıya bakın). |
| `config/query_translations.json` | Türkçe ifade/kelime → İngilizce karşılık sözlüğü. Boş `{}` da geçerlidir — çeviri sözlüğü olmadan uygulama, sorguyu değiştirmeden aramaya devam eder. |
| `config/category_taxonomy.json` | `product_quality.py`nin kullandığı genel ürün-ailesi taksonomisi (12 aile: electronics, beauty, books, automotive, home&kitchen, clothing, toys, pet, grocery, sports, tools, office) — title/category terimleri, aliaslar, `conflicting_families`. |
| `config/quality_config.json` | `product_quality.py` skorlama ağırlıkları/eşikleri, stopword listesi, flag isimleri. |

`config.py`, bu dosyaları okuyup doğrulayan (negatif limit, boş alan listesi,
geçersiz boost, bozuk çeviri/intent yapısı gibi durumları `ConfigError` ile
reddeden) ve `AppConfig` / `IntentRule` / `TranslationDictionary` gibi
immutable veri sınıflarına dönüştüren tek noktadır.

Bazı sayısal limitler ortam değişkeniyle geçersiz kılınabilir (env > JSON >
varsayılan):

- `AMAZON_SEARCH_RESULT_SIZE`
- `AMAZON_AUTOCOMPLETE_MIN_CHARS`

Yapılandırma değişikliği sonrası:

- **Lokal**: Streamlit'i yeniden başlatın (hot reload yoktur).
- **Deployment**: commit + push, Streamlit Community Cloud otomatik yeniden
  dağıtır.

Uygulama içinden config değiştirilemez; arama ayarları paneli yalnızca hangi
lexical yöntemlerin (phrase/multi-match/fuzzy/exact ASIN) ve canlı önerilerin
bu oturumda açık olduğunu seçmenizi sağlar.

## Dinamik kategori keşfi (Dynamic Category Discovery)

`intent_rules.json`, daha önce hiç tanımlanmamış ürün tipleri (`toilet
paper`, `gaming mouse`, `cat food`, `coffee grinder`, `kablosuz kulaklık`,
`tuvalet kağıdı` ...) için kategori sinyali üretemez, çünkü statik bir
kural listesidir. Bunun yerine **normal arama her çalıştığında**
(`search_products` → `resolve_intent_signals` → `discover_category_intent`),
Elasticsearch'e ana ürün index'i üzerinde `size:0`, `track_total_hits:false`,
kısa timeout'lu **tek bir aggregation isteği** gönderilir
(`build_category_discovery_query`). Bu istek `config/search_config.json`daki
`dynamic_intent.aggregation_fields` (`categories`, `main_category`,
`source_category`) üzerinde terms aggregation çalıştırır; eşleşme havuzu
olarak orijinal sorgu + normalize edilmiş sorgu + (varsa) en yüksek öncelikli
İngilizce çeviri birlikte kullanılır. Dönen en yüksek `doc_count`'lu adaylar
(`build_dynamic_category_boosts`) ana ürün sorgusunun `bool.should`'una
**boost olarak** eklenir — `bool.must` altındaki lexical zorunlu eşleşmeyi
asla bypass etmez, tek başına ürün döndürmez.

Önemli tasarım kararları:

- **Yalnızca normal aramada çalışır** — autocomplete'te (her tuş vuruşunda)
  ÇALIŞMAZ; ekstra bir aggregation isteğine gerek kalmaz.
- **Kendi cache'i vardır** (`_fetch_category_aggregations`, ayrı TTL —
  `dynamic_intent.cache_ttl_seconds`), autocomplete önerileri cache'inden
  bağımsızdır.
- **Başarısız olursa** (timeout, bağlantı hatası, min. sorgu uzunluğu altında,
  `dynamic_intent.enabled=false`) sessizce boş liste döner; ana ürün araması
  ASLA engellenmez, tek hata noktası değildir.
- `intent_rules.json`daki bir kuralın `negative_categories`'i (exclusion)
  aktifse, dinamik keşfin önerdiği aynı değerdeki kategori adayları da
  elenir — override, keşfin üzerine biner, onu değiştirmez.
- `intent_rules.json` tamamen `{}` olsa bile dinamik kategori keşfi normal
  şekilde çalışmaya devam eder (bkz. `tests/test_dynamic_category_discovery.py`).

## Sayfalama (Pagination)

Normal arama sonuçları basit, Elasticsearch-native `from + size` sayfalaması
ile sunulur (`config/search_config.json:pagination`):

```json
"pagination": {
  "enabled": true,
  "page_size": 20,
  "max_result_window": 10000,
  "max_visible_pages": 7
}
```

- `page_size` aktifken arama isteğinin `size`'ını belirler ve
  `limits.result_size`'ın yerini alır — iki alan asla çelişmez, tek bir
  öncelik kuralı vardır (`config.PaginationConfig`, `pagination.enabled=false`
  iken `limits.result_size` geçerlidir).
- **Erişilebilecek maksimum sayfa**: `max_result_window // page_size`
  (varsayılan ayarlarla 10000 // 20 = **500. sayfa**). Elasticsearch'in
  varsayılan `index.max_result_window` sınırı nedeniyle bu sınırın ötesine
  `from + size` ile gidilemez; UI "Sonraki" butonunu bu sınırda otomatik
  devre dışı bırakır ve toplam sonuç bundan fazla olsa bile kullanıcıya
  "İlk 10.000 sonuç içinde sayfalama yapılabilir" bilgisini gösterir.
  Manipüle edilmiş bir istekle (ör. doğrudan `search_products(page=...)`)
  bu sınır aşılırsa Elasticsearch'e hiç istek atılmaz;
  `app.PaginationLimitError` yakalanıp anlaşılır bir hataya çevrilir — bu bir
  çökme değil, kontrollü bir sınırdır. İleride daha derin sayfalama gerekirse
  `search_after` tabanlı imleçli sayfalamaya geçilebilir.
- **Session state**: `current_page`, sorgu metni değiştiğinde (yeni yazım,
  öneri seçimi, örnek sorgu seçimi) veya arama ayarları (phrase/multi-match/
  fuzzy/exact ASIN switch'leri) değiştiğinde 1'e sıfırlanır; "Önceki"/"Sonraki"
  butonları aynı sorgu ve aynı ayarlarla yalnızca sayfayı değiştirip aramayı
  tekrar çalıştırır (bkz. `app._resolve_page_for_new_search`).
- Autocomplete (canlı öneriler) sayfalama kullanmaz — her zaman ilk
  `autocomplete_fetch_size` sonucu getirir, sorgusunda `from` hiç yer almaz.
- İlk sürümde yalnızca Önceki/Sonraki butonları vardır; numaralı sayfa
  butonları (`max_visible_pages`) sonraki bir iterasyona bırakılmıştır.
- Sayfalama kontrolü (aralık göstergesi + Önceki/Sonraki) yalnızca ürün
  kartlarının ALTINDA render edilir; sonuç başlığı ve kartların üstünde
  tekrar çizilmez (`app.render_pagination_bar`, `main()` içinde tek bir
  çağrı — bkz. `app._RESULTS_TOP_ANCHOR_ID`). "Önceki"/"Sonraki" butonuna
  basınca `st.iframe(..., height="content")` ile çalışan best-effort bir
  scroll (`app._scroll_results_to_top`), viewport'u sonuç başlığının
  üstündeki anchor'a kaydırmayı dener; bu tamamen görseldir, başarısız
  olsa da (yakalanan herhangi bir hata sessizce yutulur) sayfalama normal
  çalışmaya devam eder.

## Arama kutusu ve canlı öneriler (`components/search_input`)

Arama kutusu ve autocomplete dropdown'u tek bir Streamlit custom component'i
(`components/search_input/`) tarafından render edilir. Daha önce kullanılan
`streamlit-keyup` bağımlılığı ve onun üzerine kurulu bir JS "bridge" hack'i
(Enter'ı yakalamak için parent iframe'e erişip Ara butonunu programatik
tıklatan best-effort script) **tamamen kaldırıldı** — `st_keyup` paketinin
tek/son sürümü (0.3.0) `onkeyup`'ta her tuş için (Enter dahil) birebir aynı
şekilde değer bildiriyordu, Enter'ı diğer tuşlardan ayıran bir sinyal yoktu;
bu da hem Enter'ın güvenilmez çalışmasına hem de kırılgan bir hack'e yol
açıyordu.

Yerine, build-step gerektirmeyen (st_keyup'ın kendi yaklaşımıyla aynı,
saf HTML/JS) yerel bir component geldi:

- `components/search_input/frontend/index.html` — tek görünür kutu + altında
  Chrome/Google tarzı tek bir öneri paneli (kompakt satırlar: görsel, başlık,
  gri meta satırı, hover highlight, ellipsis, yukarı/aşağı ok + Enter ile
  klavye navigasyonu, Escape ile kapatma). Kendi `keydown` olayını yalnızca
  KENDİ iframe'i içinde dinler — parent iframe'e erişim veya başka bir
  Streamlit widget'ını programatik tıklatma YOKTUR.
- Python ↔ JS arasında sabit bir JSON event sözleşmesi vardır (bkz.
  `components/search_input/CONTRACT.md`):
  `{"type": "typing"|"submit"|"select", "query": str, "event_id": str, "asin": str|None}`.
  `event_id` her olayda benzersizdir; Streamlit aynı değeri iki kez
  görürse rerun atlayabileceğinden bu, aynı sorgunun art arda iki kez
  aranması gibi durumlarda bile bir rerun'un kaçmamasını garanti eder.
- `app._handle_search_input_event`, bu olayı işler: **"submit"** (panelde
  aktif öneri yokken Enter) ve **"select"** (bir öneriye tıklama VEYA
  panelde aktif öneri varken Enter) `_trigger_explicit_search`/`_select_query`
  üzerinden **Ara butonuyla TAMAMEN AYNI** state geçişini kullanır. **"typing"**
  hiçbir zaman aramayı tetiklemez — yalnızca canlı öneri panelinin
  güncellenmesi için güncel metni bildirir. Aynı `event_id` tekrar
  görülürse (component'le ilgisiz bir rerun) hiçbir şey yeniden tetiklenmez.
- Component tek başına hiçbir arama/autocomplete iş mantığı içermez
  (Elasticsearch sorgusu yok, config okuma yok, session_state yok); yalnızca
  generic olayları Python'a bildirir. Arama/autocomplete mantığı
  `services/` katmanındadır (aşağıya bakın) ve yalnızca `app.py` üzerinden
  kullanılır — component'in kendisi bu servisleri DOĞRUDAN çağırmaz.
  Bu ayrım, `frontend/`in ileride bir React build'iyle değiştirilmesini
  (aynı sözleşme korunarak) kolaylaştırır.
- Panelin görsel ayarları (`panel_max_height_px`, `row_height_px`,
  `show_images`) `config/search_config.json:autocomplete_ui`den gelir; öneri
  sayısı `limits.autocomplete_display_size`den gelir (değişmedi).

**Bilinen davranış**: `search_input` component'i tek bir Streamlit widget
olduğundan (aynı `key` ile bir run içinde iki kez çağrılamaz), yeni
hesaplanan öneriler aynı run'da component'in `suggestions` prop'una geri
beslenemez; `app.main()` bunları `session_state`e yazıp tek bir ekstra
(Elasticsearch isteği İÇERMEYEN) `st.rerun()` ile bir sonraki run'a taşır.
Bu, panelin her zaman en son yazılan metne ait önerileri göstermesini
garanti eder.

## Ürün veri kalitesi / title-category tutarlılığı (`product_quality.py`)

`product_quality.py`, tek tek ürün türüne özel intent kuralı yazmadan,
başlık ile kategori arasındaki tutarsızlıkları (ör. başlık "Gaming Mouse
Black" ama kategori "Beauty & Personal Care / Makeup Brushes") genel bir
kategori-ailesi taksonomisiyle (`config/category_taxonomy.json`, 12 ürün
ailesi) tespit eder. Elasticsearch'e bağlanmaz — saf, deterministik,
`evaluate_product_quality(product: dict) -> dict` fonksiyonu:

```json
{
  "title_category_consistency": 0.0-1.0,
  "data_quality_score": 0.0-1.0,
  "quality_flags": ["title_category_mismatch", "..."],
  "quality_version": "v1"
}
```

Algoritma: başlık ve kategori metinleri taksonomideki ailelere karşı
puanlanır (phrase eşleşmesi token eşleşmesinden ağır sayılır, jenerik
terimler puan katmaz); bir taraf için "baskın aile" yalnızca yeterli sinyal
VE ikinci adaya karşı yeterli marj varsa belirlenir — bu, tek kelimelik
belirsiz sinyallerden (ör. bağlamsız "watch", "light", "chair") yanlış
pozitif üretilmesini engeller. Aileler farklı VE
`config/category_taxonomy.json`daki `conflicting_families` listesinde
birbirine karşıtsa `title_category_mismatch` flag'i eklenir; farklı ama
karşıt tanımlı değilse (ör. "book light", "pet hair vacuum" gibi meşru
çok-alanlı ürünler) tamamen eleme değil, skor düşürme uygulanır. Ağırlıklar
ve eşikler `config/quality_config.json`dan gelir.

- **Importer entegrasyonu**: `full_amazon_importer.py` ve `index_amazon.py`,
  her belge bulk-index edilmeden önce `evaluate_product_quality`yi çağırır
  (`apply_quality_evaluation`). Hata durumunda importu durdurmaz; güvenli
  fallback (`data_quality_score=0.5`, flag `quality_evaluation_failed`)
  uygulanır ve hata loglanır. Checkpoint/resume davranışı değişmedi.
- **Arama entegrasyonu**: `app.py`, `config/search_config.json:quality_ranking`
  (varsayılan `enabled:false` — production index'lerinde henüz kalite
  alanı yok) açıkken normal arama sorgusunu bir `function_score` ile
  sarmalar (`field_value_factor` boost + eşik-altı `filter`+`weight`
  penalty; `script_score` KULLANILMAZ). Lexical zorunlu eşleşme
  (`bool.must`) hiç değişmez — kalite yalnızca zaten eşleşmiş belgeleri
  yeniden sıralar. Exact ASIN sorgusu (`bypass_for_exact_asin`) kalite
  boost/penalty'sinden muaftır. Kalite alanı olmayan eski belgeler sorguyu
  bozmaz (`missing_value_behavior`). Autocomplete etkilenmez.
- **Dinamik kategori keşfi koruması**: `quality_ranking.discovery_filter_enabled`
  (varsayılan `false`) açıldığında, `build_category_discovery_query`
  düşük `data_quality_score`'lu belgeleri aggregation örnekleminden
  dışlar — böylece keşif, bozuk title-category eşleşmelerinden yanlış
  kategori öğrenmez. Eski index'lerde alan yoksa bu filtre no-op'tur.
- **Offline değerlendirme**: `evaluate_quality_sample.py`, Elasticsearch'ten
  read-only örnek çekip CSV/JSONL kalite raporu üretir (hiçbir belgeyi
  güncellemez).
- **Production migration planı**: `elasticsearch/product_quality_production_migration.md`
  — mevcut ~5.9M belgeye kalite alanlarını eklemek için gereken reindex/
  backfill stratejisini, yeni mapping tasarımını (neden `rank_feature`
  değil `float` seçildiğini) ve aşama aşama onay noktalarını içerir. Bu
  görevde yalnızca read-only `_mapping`/`_settings`/`_count`/`_stats`
  sorguları çalıştırıldı; hiçbir yazma/reindex işlemi yapılmadı.

## Mimari

- **`services/search_service.py`** — sorgu üretimi (`build_search_query`),
  manuel intent tespiti (`detect_search_intent`), dinamik kategori keşfi
  (`discover_category_intent`, `build_category_discovery_query`,
  `build_dynamic_category_boosts`, `resolve_intent_signals`), Türkçe→İngilizce
  sorgu genişletme (`expand_multilingual_query`), kalite reranking ve
  `search_products` (ES'e giden GERÇEK istek). **Streamlit'e bağımlı
  DEĞİLDİR** (import yok, `session_state` yok) — ileride bir FastAPI
  endpoint'i de aynı fonksiyonları doğrudan çağırabilir. Önbellekleme
  (`st.cache_data`) burada değil `app.py`de yapılır: `discover_category_intent`/
  `search_products`, `fetch_aggregations` adlı bir dependency-injection
  parametresi kabul eder — varsayılanı önbelleksiz gerçek Elasticsearch
  çağrısıdır, `app.py` kendi cache'li sarmalayıcısını (`app._fetch_category_aggregations`)
  enjekte ederek üretimdeki önbellekleme davranışını korur.

  Zorunlu eşleşme her zaman `bool.must` içindeki lexical `bool.should`
  grubundadır; intent/kategori boostları (manuel + dinamik) yalnızca dış
  `bool.should`'da (rerank-only) yer alır, `bool.must_not` ise kontrollü
  dışlamalar içindir. Çeviri alternatifleri zorunlu eşleşmeyi **atlamaz**,
  ona ek bir seçenek olarak eklenir.

  `build_search_query` **saf** (Elasticsearch'e istek atmayan) bir
  fonksiyondur — dinamik keşfin ürettiği sinyaller ona parametre olarak
  enjekte edilir (`intent_boost_queries`/`intent_exclusions`). Gerçek ağ
  çağrısını içeren orkestrasyon `search_products` içindedir. Bu ayrım,
  `build_search_query`'nin testlerde canlı cluster'a istek atmadan doğrudan
  çağrılabilmesini sağlar. `search_products`, sayfalama metadata'sı içeren
  bir `SearchResult` (`hits`/`total`/`error`/`current_page`/`page_size`/
  `total_pages`/`start_item`/`end_item`/`has_previous`/`has_next`) döner
  (bkz. yukarıdaki "Sayfalama" bölümü, tip tanımı `services/search_models.py`de).
- **`services/autocomplete_service.py`** — Edge NGram autocomplete sorgusu
  (`build_autocomplete_query`) ve öneri listesi üretimi (`get_suggestions`,
  `SuggestionItem` listesi döner). `search_service`e her zaman MODÜL
  REFERANSIYLA bağımlıdır (`from services import search_service`, `from
  services.search_service import X` DEĞİL) — aksi halde testlerin/`app.py`nin
  `search_service` üzerinde yaptığı config/mock değişiklikleri bu modülden
  görünmez olurdu. Streamlit'e bağımlı değildir.
- **`services/search_models.py`** — `SearchResult`, `SuggestionItem`,
  `PaginationLimitError`: iki servis modülü arasında paylaşılan, JSON-safe
  (yalnızca primitive/list/dict alanlar) veri sözleşmeleri.
- **`components/search_input/`** — arama kutusu + autocomplete dropdown
  custom component'i (bkz. yukarıdaki "Arama kutusu ve canlı öneriler"
  bölümü). UI-only; hiçbir arama iş mantığı içermez.
- **`app.py`** — Streamlit arayüzü ve `session_state` orkestrasyonu.
  Yukarıdaki `services`/`components` paketlerini çağıran ince bir katmandır;
  test/geri-uyum kolaylığı için `services.search_service`/`autocomplete_service`nin
  üst seviye fonksiyonlarını kendi isim alanında da re-export eder (ör.
  `app.build_search_query(...)` doğrudan çalışır), ama GERÇEK çağrılar
  (`main()` içinde) önbellekli fetcher'ları enjekte etmek için her zaman
  `search_service.X(...)`/`autocomplete_service.X(...)` şeklinde modül
  referansıyla yapılır.
- **`config.py`** — config dosyalarının yükleme/doğrulama katmanı
  (`quality_ranking`, `autocomplete_ui` dahil).
- **`product_quality.py`** — ürün veri kalitesi / title-category tutarlılığı
  değerlendirmesi (bkz. yukarıdaki bölüm). Elasticsearch'e bağlanmaz.
- **`full_amazon_importer.py`** — Hugging Face'teki `McAuley-Lab/Amazon-Reviews-2023`
  veri setinin tamamını, checkpoint'li ve devam ettirilebilir şekilde,
  her belge için `product_quality.evaluate_product_quality` çalıştırarak
  Elasticsearch'e (`amazon-products` alias'ı) indexler.
- **`index_amazon.py`** — kategori başına 10 ürünlük eski/basit örnek importer
  (aynı `product_quality` modülünü kullanır, kod kopyalamaz).
- **`inspect_amazon.py`** — Elasticsearch'e bağlanmadan, ham Hugging Face
  kayıtlarının alan yapısını incelemek için debug scripti; `--quality`
  bayrağıyla örnek kayıtların kalite değerlendirmesini de yazdırır.
- **`evaluate_quality_sample.py`** — Elasticsearch'ten read-only örnek çekip
  kalite raporu (CSV/JSONL) üreten offline analiz aracı; hiçbir belgeyi
  güncellemez.

Index mapping/analyzer tanımları (Edge NGram, Türkçe analyzer vb.) bu depoda
değil, doğrudan Elastic Cloud cluster'ında (Kibana Dev Tools üzerinden)
tutulur.

## Elasticsearch indexleri

Normal arama:

```text
amazon-products-000001
amazon-products-000002
```

Autocomplete (Edge NGram):

```text
amazon-products-autocomplete-000001
amazon-products-autocomplete-000002
```

## Reindex / analyzer değişiklikleri

Analyzer veya mapping değişiklikleri mevcut belgeleri geriye dönük
etkilemez. Büyük ölçekli reindex işlemleri bu depodaki hiçbir script
tarafından otomatik başlatılmaz; production index'lerinde değişiklik
yapmadan önce yeni bir test index'i üzerinde doğrulama yapılmalı ve
kullanıcı onayı alınmalıdır.

### Türkçe stemmer hazırlığı

Query translation (`expand_multilingual_query`), Türkçe→İngilizce **anlam**
çevirisi yapar; `telefon`/`telefonlar`/`telefonların` gibi **çekim eki**
varyasyonlarını çözmez (bu, ayrı bir problemdir: Türkçe morphology/stemming).
Bunun için Elasticsearch'ün genel `stemmer` (`language: turkish`) filtresini
kullanan bir analyzer önerisi, izole bir **test index'i** oluşturma komutu ve
`_analyze` doğrulama sorguları hazırlanmıştır:

```text
elasticsearch/turkish_stemmer_test_plan.md
```

Bu dosyadaki hiçbir komut bu depo/görev kapsamında çalıştırılmadı; production
mapping'i değiştirilmedi, reindex başlatılmadı. Komutlar Kibana Dev Tools'a
yapıştırılıp kullanıcı tarafından manuel doğrulanmalıdır.
