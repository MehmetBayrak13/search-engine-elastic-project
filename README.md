# Amazon Elasticsearch Product Search

Elastic Cloud destekli, Streamlit tabanlı ürün arama uygulaması. Amazon Reviews
2023 veri setinden indexlenmiş ürünler üzerinde exact ASIN, phrase, multi-field
ve fuzzy arama; Edge NGram autocomplete; Elasticsearch aggregation tabanlı
dinamik kategori keşfi; opsiyonel manuel intent override katmanı ve
Türkçe→İngilizce sorgu genişletme destekler.

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
| `config/search_config.json` | Index adları, timeout/limit değerleri, exact ASIN / phrase / multi-match / fuzzy / autocomplete alan ve boost ayarları, çeviri ayarları, **dinamik kategori keşfi (`dynamic_intent`) ayarları**, dönen kaynak alanlar, arayüz metinleri |
| `config/intent_rules.json` | **Opsiyonel override katmanı** (örn. `watch`): alias/tetikleyici terimler, dışlama koşulları, force-boost terimleri, negatif kategoriler (exclusion), rozet metni/ikonu, `priority`. Boş `{}` da geçerlidir — hiç kural olmadan da çalışır; ana kategori-intent motoru bu dosya DEĞİL, dinamik kategori keşfidir (aşağıya bakın). |
| `config/query_translations.json` | Türkçe ifade/kelime → İngilizce karşılık sözlüğü. Boş `{}` da geçerlidir — çeviri sözlüğü olmadan uygulama, sorguyu değiştirmeden aramaya devam eder. |

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
  çağrılabilmesini sağlar.
- **`config.py`** — config dosyalarının yükleme/doğrulama katmanı.
- **`full_amazon_importer.py`** — Hugging Face'teki `McAuley-Lab/Amazon-Reviews-2023`
  veri setinin tamamını, checkpoint'li ve devam ettirilebilir şekilde
  Elasticsearch'e (`amazon-products` alias'ı) indexler.
- **`index_amazon.py`** — kategori başına 10 ürünlük eski/basit örnek importer.
- **`inspect_amazon.py`** — Elasticsearch'e bağlanmadan, ham Hugging Face
  kayıtlarının alan yapısını incelemek için debug scripti.

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
