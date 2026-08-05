# Amazon Elasticsearch Product Search

Elastic Cloud destekli, Streamlit tabanlı ürün arama uygulaması. Amazon Reviews
2023 veri setinden indexlenmiş ürünler üzerinde exact ASIN, phrase, multi-field
ve fuzzy arama; Edge NGram autocomplete; Elasticsearch aggregation tabanlı
dinamik kategori keşfi; opsiyonel manuel intent override katmanı; `from + size`
tabanlı sayfalama ve Türkçe→İngilizce sorgu genişletme destekler.

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
python -m py_compile app.py config.py
python -m pytest -q
```

## Yapılandırma

Sırlar (`ELASTICSEARCH_URL`, `ELASTICSEARCH_API_KEY`) her zaman ortam
değişkeninden veya Streamlit Secrets'tan gelir; asla dosyaya yazılmaz.

Arama davranışının tamamı `config/` altındaki JSON dosyalarından okunur —
`app.py` içinde index adı, boost değeri veya arayüz metni hardcoded değildir:

| Dosya | İçerik |
|---|---|
| `config/search_config.json` | Index adları, timeout/limit değerleri, exact ASIN / phrase / multi-match / fuzzy / autocomplete alan ve boost ayarları, çeviri ayarları, **dinamik kategori keşfi (`dynamic_intent`) ayarları**, **sayfalama (`pagination`) ayarları**, dönen kaynak alanlar, arayüz metinleri |
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

- **`app.py`** — Streamlit arayüzü, sorgu üretimi (`build_search_query`,
  `build_autocomplete_query`), manuel intent tespiti (`detect_search_intent`),
  dinamik kategori keşfi (`discover_category_intent`,
  `build_category_discovery_query`, `build_dynamic_category_boosts`,
  `resolve_intent_signals`) ve Türkçe→İngilizce sorgu genişletme
  (`expand_multilingual_query`). Zorunlu eşleşme her zaman `bool.must`
  içindeki lexical `bool.should` grubundadır; intent/kategori boostları
  (manuel + dinamik) yalnızca dış `bool.should`'da (rerank-only) yer alır,
  `bool.must_not` ise kontrollü dışlamalar içindir. Çeviri alternatifleri
  zorunlu eşleşmeyi **atlamaz**, ona ek bir seçenek olarak eklenir.

  `build_search_query` **saf** (Elasticsearch'e istek atmayan) bir
  fonksiyondur — dinamik keşfin ürettiği sinyaller ona parametre olarak
  enjekte edilir (`intent_boost_queries`/`intent_exclusions`). Gerçek ağ
  çağrısını içeren orkestrasyon `search_products` içindedir. Bu ayrım,
  `build_search_query`'nin testlerde canlı cluster'a istek atmadan doğrudan
  çağrılabilmesini sağlar. `search_products`, sayfalama metadata'sı içeren
  bir `SearchResult` (`hits`/`total`/`error`/`current_page`/`page_size`/
  `total_pages`/`start_item`/`end_item`/`has_previous`/`has_next`) döner
  (bkz. yukarıdaki "Sayfalama" bölümü).
- **`config.py`** — config dosyalarının yükleme/doğrulama katmanı
  (`quality_ranking` dahil).
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
