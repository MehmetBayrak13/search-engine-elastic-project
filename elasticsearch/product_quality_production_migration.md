# Ürün Veri Kalitesi — Production Migration Planı

Bu dosya **yalnızca bir plandır**. Aşağıdaki adımlardan hiçbiri bu görev
kapsamında çalıştırılmadı:

- Production index'lerine (`amazon-products-000001`, `amazon-products-000002`,
  `amazon-products-autocomplete-000001`, `amazon-products-autocomplete-000002`)
  **hiçbir yazma/mapping değişikliği yapılmadı.**
- Hiçbir yeni index oluşturulmadı/silinmedi.
- Hiçbir `_reindex` başlatılmadı.
- Hiçbir alias değiştirilmedi.
- `config/search_config.json` yeni indexlere yönlendirilmedi
  (`quality_ranking.enabled=false`, `discovery_filter_enabled=false` olarak
  bırakıldı — bkz. dosyanın kendisi).

Bu görev kapsamında cluster üzerinde **yalnızca read-only** istekler
çalıştırıldı: `GET .../_mapping`, `GET .../_settings`, `GET .../_count`,
`GET .../_stats/store`. Sonuçlar aşağıda §1'de özetlenmiştir.

---

## 0. Kapsam

Bu plan yalnızca **normal arama index'lerini** (`amazon-products-000001`,
`amazon-products-000002`) kapsar. Autocomplete index'leri
(`amazon-products-autocomplete-000001/000002`) şu anki entegrasyonda
kalite alanlarına ihtiyaç DUYMAZ — `app.py`'de kalite reranking'i yalnızca
`build_search_query` (normal arama) sarmalar; `build_autocomplete_query`
bilerek etkilenmez (bkz. `tests/test_quality_ranking.py::test_autocomplete_query_unaffected_by_quality_ranking`).
Autocomplete'e kalite sinyali eklemek istenirse bu, ayrı ve daha sonraki bir
karardır.

---

## 1. Mevcut mapping/settings/count read-only kontrolü (ÇALIŞTIRILDI)

`GET amazon-products-000001,amazon-products-000002/_mapping` ve `_settings`,
`_count`, `_stats/store` ile doğrulanan gerçek durum:

| Alan | Tip |
|---|---|
| `title` | `text` (`standard` analyzer) + `.keyword` (lowercase normalizer) + `.tr` (`turkish_product_analyzer`) |
| `main_category` | `keyword` (lowercase normalizer) |
| `categories` | `keyword` (lowercase normalizer) |
| `categories_text` | `text` (`standard`) + `.tr` (`turkish_product_analyzer`) |
| `source_category` | `keyword` (lowercase normalizer) |
| `description` | `text` (`standard`) + `.tr` |
| `features` | `text` (`standard`) + `.tr` |
| `store` | `keyword` (lowercase normalizer) |
| `parent_asin` | `keyword` |
| `price` | `double` |
| `average_rating` | `half_float` |
| `rating_number` | `integer` |
| `image_url` | `keyword` (`index:false`) |

Bu 13 alan, `config/search_config.json:source_fields.search` ile birebir
örtüşüyor — `product_quality.py`'nin ihtiyaç duyduğu tüm girdi alanları
(`title`, `main_category`, `categories`, `categories_text`,
`source_category`, `description`, `features`, `store`, `parent_asin`)
zaten mevcut ve doğru tipte. **Kalite alanı (`title_category_consistency`,
`data_quality_score`, `quality_flags`, `quality_version`) hiçbir index'te
YOK.**

Ölçek/boyut (`_count`, `_stats/store`):

| Index | Belge sayısı (toplam, iki index) | Disk boyutu | Shard/Replica |
|---|---|---|---|
| `amazon-products-000001` | 5.913.257 (toplam) | 7.50 GB | 1 shard / 0 replica |
| `amazon-products-000002` | (yukarıdaki toplama dahil) | 3.02 GB | 1 shard / 0 replica |

Toplam: **~5.91 milyon belge, ~10.52 GB**. Not: replika 0 olduğu için
production'da tek node kaybı veri kaybına yol açabilir — bu, kalite
migration'ının kapsamı dışında ama ayrıca not edilmeye değer bir gözlem.

---

## 2. Offline örnek değerlendirme

`evaluate_quality_sample.py` (bu görevde eklendi, **çalıştırılmadı** —
`_search` bu görevin izin verilen read-only komut listesinde değildi, bkz.
görev sınırları) kullanıcı tarafından şöyle çalıştırılmalı:

```bash
export ELASTICSEARCH_URL="..."
export ELASTICSEARCH_API_KEY="..."
python evaluate_quality_sample.py --sample-size 500 --seed 42 --output quality_sample.jsonl
python evaluate_quality_sample.py --query "gaming mouse" --sample-size 100 --output quality_sample_gaming_mouse.csv
```

Bu, gerçek production verisinde `title_category_mismatch` oranını,
`generic_title`/`missing_*` flag dağılımını ve `title_category_consistency`
histogramını görmeden eşik/ağırlık ayarlamadan önce kalibrasyon sağlar.
Script hiçbir belgeyi günceller.

---

## 3. Eşiklerin gözden geçirilmesi

`config/quality_config.json` (`consistency_scoring`, `weights`,
`completeness_signals`, `validity_signals`) ve
`config/search_config.json:quality_ranking` (`boost`, `consistency_boost`,
`low_consistency_threshold`, `low_consistency_penalty`) değerleri, §2'deki
örneklem raporu incelendikten SONRA ayarlanmalı. Özellikle:

- `low_consistency_threshold` (varsayılan 0.3) — örneklemdeki gerçek
  `title_category_consistency` dağılımına göre kalibre edilmeli (çok düşükse
  penalty hiç tetiklenmez, çok yüksekse meşru çok-alanlı ürünler cezalanır).
- `dominance_margin_ratio` / `min_family_signal`
  (`consistency_scoring` içinde) — false-positive/false-negative dengesini
  kontrol eder; §2 raporundaki yanlış flag'lenmiş örnekler bu değerleri
  ayarlamak için kullanılmalı.
- `category_taxonomy.json`daki aile terim listeleri, gerçek kategori
  isimleriyle (Amazon'un gerçek `main_category`/`categories` string'leri)
  karşılaştırılıp genişletilmeli — bu görevde yalnızca temsili terimlerle
  dolduruldu.

Bu adım kullanıcı onayı ve gerçek veri incelemesi gerektirir; kod
değişikliği yalnızca JSON dosyalarında yapılır, `product_quality.py`
değişmeden kalabilir.

---

## 4. Yeni index mapping'lerinin hazırlanması

### 4.1 Yeni alanlar

```json
{
  "properties": {
    "title_category_consistency": { "type": "float" },
    "data_quality_score": { "type": "float" },
    "quality_flags": { "type": "keyword" },
    "quality_version": { "type": "keyword" }
  }
}
```

### 4.2 Neden `float`, neden TEK alan (rank_feature YOK)

`rank_feature` yerine düz `float` seçildi, gerekçe:

1. **Filtreleme ihtiyacı** — §8 (dynamic discovery quality filter) düşük
   kaliteli belgeleri aggregation örnekleminden `range` sorgusuyla dışlamayı
   gerektiriyor (`must_not: {"range": {"data_quality_score": {"lt": ...}}}`).
   `rank_feature` tipi **sorgulanamaz/filtrelenemez** (yalnızca
   `rank_feature` query içinde, salt reranking amaçlı kullanılabilir) — bu
   gereksinimi karşılayamaz.
2. **Eşik-bazlı penalty ihtiyacı** — §7 (`low_consistency_threshold`/
   `low_consistency_penalty`) bir `function_score` fonksiyonunun `filter`
   kısmında `range` sorgusu gerektiriyor; bu da `rank_feature` ile mümkün
   değil.
3. **Boost ihtiyacı zaten `field_value_factor` ile karşılanıyor** —
   `function_score.functions[].field_value_factor` düz sayısal alanlar
   üzerinde doğrudan çalışır (rank_feature'a ÖZEL bir mekanizma değildir);
   `script_score` gerekmez (bkz. `app.py:_build_quality_functions`).
4. **Tek alan, çift depolama yok** — aynı bilgi (`data_quality_score`,
   `title_category_consistency`) hem filtreleme hem boost için TEK bir
   `float` alanda tutulur; `rank_feature` + ayrı bir `float` kopyası
   tutmak (spec'in kaçınılmasını istediği "aynı bilginin iki kez
   saklanması") gereksiz olurdu.

Sonuç: en sade ve performanslı tasarım, **her iki sinyal için de tek bir
`float` alan** — hem boost (`field_value_factor`) hem filtre (`range`) aynı
alanı kullanır, script_score'a gerek kalmaz.

`quality_flags` → `keyword` (çoklu değer, `terms`/`term` filtreleri ve
`evaluate_quality_sample.py` gibi offline analiz için). `quality_version` →
`keyword` (ileride algoritma değişirse eski/yeni belgeleri ayırt etmek
için — bkz. §14 rollback).

### 4.3 Yeni index adı önerisi

Mevcut isimlendirme deseni (`amazon-products-000001/000002`) bir
rollover/alias yapısına işaret ediyor olabilir; bu görev kapsamında alias
bilgisi (`GET _alias`) izin verilen read-only komut listesinde olmadığı
için SORGULANMADI. Bu nedenle kesin index adı **kullanıcı/DevOps ekibiyle
teyit edilmeli**. Öneri:

```text
amazon-products-quality-v1-000001   (000001'in yeniden index'lenmiş hâli)
amazon-products-quality-v1-000002   (000002'nin yeniden index'lenmiş hâli)
```

---

## 5. Yeni index'lerin oluşturulması

```http
PUT amazon-products-quality-v1-000001
{
  "settings": { "number_of_shards": 1, "number_of_replicas": 0 },
  "mappings": { "properties": { /* mevcut 13 alan (§1) + §4.1'deki 4 yeni alan */ } }
}
```

Mevcut analyzer/normalizer tanımları (`turkish_product_analyzer`,
`lowercase_normalizer`, autocomplete analyzer'ları) **birebir kopyalanmalı**
— bu görev onları değiştirmedi/reindex etmedi (Türkçe stemmer konusu ayrı
bir iştir, bkz. `elasticsearch/turkish_stemmer_test_plan.md`).

**Bu adım bu görevde ÇALIŞTIRILMADI. Kullanıcı onayı gerekir.**

---

## 6-7. Mevcut belgeler için kalite alanlarının hesaplanması + reindex/backfill stratejisi

### Seçenek karşılaştırması

| Seçenek | Açıklama | Değerlendirme |
|---|---|---|
| **A** — Scroll/search_after + Python evaluator + bulk index | Mevcut index'i oku, `evaluate_product_quality` çalıştır, TÜM belgeyi yeni index'e yaz | Çalışır ama her backfill denemesinde 10.5 GB'lık belgeyi yeniden ağdan taşır — eşik/config ayarlaması (§3) her tekrarında pahalı |
| **B** — Elasticsearch ingest pipeline (Painless script) | Kalite algoritmasını Painless'e taşı | **Reddedildi**: `category_taxonomy.json` (12 aile, ~400 terim) ve tüm ağırlık/eşik mantığını Painless'te yeniden yazmak, iki dilde aynı algoritmayı senkron tutmayı gerektirir — bakım yükü ve tutarsızlık riski yüksek. Ayrıca Painless'in dosya sisteminden JSON config okuma imkânı yoktur (script içine gömülmesi gerekir) |
| **C** — `full_amazon_importer.py` ile Hugging Face'ten yeniden import | Kalite entegrasyonu zaten eklendi (bkz. `apply_quality_evaluation`); checkpoint'li/resumable | Çalışır ama GEREKSİZ — kaynak veri değişmedi, ~5.9M belgeyi tekrar Hugging Face'ten indirmek bant genişliği/süre israfı ve HF erişilebilirliğine gereksiz bağımlılık |
| **D** — Basit `_reindex` + ayrı Python backfill (update) | (1) ES-native `_reindex` ile yapısal kopya (yeni mapping, kalite alanları henüz boş), (2) Python `search_after` + `evaluate_product_quality` + `_bulk` **update** (yalnızca 4 kalite alanı) | **Seçildi** (aşağıda gerekçelendirilmiştir) |

### Seçilen yaklaşım: D (iki fazlı)

**Faz 1 — Yapısal reindex (ES-native, Python'a gerek yok):**

```http
POST _reindex?wait_for_completion=false&requests_per_second=2000
{
  "source": { "index": "amazon-products-000001", "size": 1000 },
  "dest": { "index": "amazon-products-quality-v1-000001", "op_type": "create" }
}
```
(`amazon-products-000002` için aynısı, ayrı bir task.)

**Faz 2 — Python kalite backfill (yalnızca 4 alanlık partial update):**

```python
# Sözde kod — gerçek script bu görevde YAZILMADI/ÇALIŞTIRILMADI
for batch in scroll_with_search_after(new_index, sort=["_doc"], batch_size=1000):
    actions = []
    for doc in batch:
        quality = evaluate_product_quality(doc["_source"])
        actions.append({
            "_op_type": "update",
            "_index": new_index,
            "_id": doc["_id"],
            "doc": quality,   # yalnızca 4 alan — tüm belge değil
        })
    bulk(client, actions)
    save_checkpoint(last_sort_value)
```

### Neden bu hibrit (D), tek-fazlı A yerine?

1. **Ayrık yeniden-denenebilirlik** — §3'teki eşik/ağırlık ayarlaması
   iteratif olacaktır. Faz 1 (yapısal kopya) BİR KEZ yapılır; Faz 2
   (kalite hesaplama) mapping/config her değiştiğinde YENİDEN çalıştırılsa
   bile Faz 1'i tekrarlamaz — 10.5 GB'lık belgeyi tekrar taşımaz, yalnızca
   4 küçük alanı günceller (çok daha az bant genişliği/süre).
2. **ES-native hız** — Faz 1, `_reindex`'in dahili toplu kopyalama
   optimizasyonlarından (Python döngüsü olmadan) yararlanır; `_task` API'si
   ile native olarak izlenebilir.
3. **`full_amazon_importer.py`'nin checkpoint desenine paralel** — Faz 2,
   mevcut projedeki checkpoint/resume/hata-loglama desenini (bkz.
   `amazon_import_checkpoint.json`, `import_errors.jsonl`) yeniden kullanan
   ayrı, küçük bir backfill script'i olarak yazılabilir (bu görevde
   YAZILMADI — yalnızca planlanmıştır).
4. **script_score/Painless yok** — kalite hesabı tamamen Python'da
   (`product_quality.py`) kalır; §6-7 karşılaştırmasının B seçeneğinde
   belirtilen ikili-bakım riskinden kaçınılır.

### Ölçek tahmini

- **İşlenecek belge sayısı**: ~5.91 milyon (yalnızca 2 normal arama
  index'i; autocomplete kapsam dışı, bkz. §0).
- **Disk**: Faz 1 kabaca kaynakla aynı boyutta yeni bir index yaratır
  (~10.5 GB) — geçici olarak eski+yeni birlikte ~21 GB gerekir. Faz 2, 4
  küçük alan ekler (`float`×2 + `keyword` + `keyword[]`); belge başına
  tahminen +50-150 byte → toplam **+300-900 MB** (ihmal edilebilir, ~21 GB
  toplamın yanında).
- **Süre**: Faz 1 (`_reindex`, native, `requests_per_second` ile
  throttled) tipik olarak saatler mertebesinde (cluster kapasitesine
  bağlı). Faz 2 (Python, saf fonksiyon + bulk update) belge başına
  ~1-3 ms `evaluate_product_quality` + ağ round-trip; 5.91M belge için
  kabaca **birkaç saat ile bir gün arası** (bulk batch boyutu ve cluster
  yüküne bağlı) — `full_amazon_importer.py`'nin zaten "saatler/günler
  sürebilir" şeklinde tasarlanmış olmasıyla tutarlı bir büyüklük mertebesi.
- **Risk**: Faz 1 ve 2 ayrı olduğundan, Faz 2 sırasında bir hata/timeout
  yalnızca kalite alanlarını etkiler — temel arama verisi (Faz 1 çıktısı)
  bozulmaz. Faz 2 kendi checkpoint'iyle kaldığı yerden devam edebilir.

**Bu görevde ne Faz 1 ne Faz 2 ÇALIŞTIRILDI.**

---

## 8. Task/progress takibi

- Faz 1: `GET _tasks/<task_id>` (native ES task API) ile izlenir.
- Faz 2: `full_amazon_importer.py`daki desene paralel bir
  `quality_backfill_checkpoint.json` (son işlenen `search_after` değeri,
  toplam işlenen/hatalı sayaç) + `quality_backfill_errors.jsonl` önerilir.

---

## 9. Kaynak/hedef count karşılaştırması

Faz 1 sonrası:
```http
GET amazon-products-000001,amazon-products-000002/_count
GET amazon-products-quality-v1-000001,amazon-products-quality-v1-000002/_count
```
İki sonuç **birebir eşit** olmalı (5.913.257). Eşit değilse cutover'a
geçilmez.

Faz 2 sonrası, `quality_version` alanı üzerinden örnek doğrulama:
```http
GET amazon-products-quality-v1-000001,amazon-products-quality-v1-000002/_count
{ "query": { "term": { "quality_version": "v1" } } }
```
Bu sayı da toplam belge sayısına eşit olmalı (her belge işlendi mi kontrolü).

---

## 10. İngilizce/Türkçe/ASIN regresyon testleri

Cutover öncesi, yeni index'e karşı (mevcut `tests/` paketindeki testlerin
canlı-cluster eşdeğerleri, manuel/Kibana Dev Tools ile):

- İngilizce sorgular: `wireless headphones`, `gaming mouse`, `coffee grinder`
  (mevcut `example_queries`) — sonuç sayısı/ilk 5 sonucun mevcut index ile
  kıyaslanması (kalite reranking KAPALIYKEN aynı, AÇIKKEN sıralama
  değişebilir ama alaka düzeyi bozulmamalı).
- Türkçe sorgular ve çeviri genişletmesi: `kablosuz kulaklık`,
  `tuvalet kağıdı`, `akıllı saat` — `expand_multilingual_query` davranışı
  kalite entegrasyonundan etkilenmez (ayrı katman), ama uçtan uca
  doğrulanmalı.
- Exact ASIN: bilinen birkaç `parent_asin` değeriyle arama —
  `quality_ranking.bypass_for_exact_asin=true` iken sonucun ilk sırada
  kaldığının doğrulanması (bkz. `tests/test_quality_ranking.py::test_exact_asin_bypass_excludes_matching_document_from_all_functions`
  — birim test seviyesinde zaten kanıtlandı, burada canlı cluster'da
  tekrar doğrulanmalı).
- Autocomplete: değişmemesi beklenir (kapsam dışı, §0).

---

## 11. Search config geçişi

`config/search_config.json`:
```json
"elasticsearch": {
  "search_indices": ["amazon-products-quality-v1-000001", "amazon-products-quality-v1-000002"]
}
```
Bu değişiklik **yalnızca** §9-10 doğrulamaları geçtikten ve kullanıcı onayı
alındıktan sonra yapılmalı. **Bu görevde yapılmadı.**

---

## 12. Quality ranking flag'inin açılması

```json
"quality_ranking": { "enabled": true, ... }
```
§11'den SONRA, önce düşük bir `boost`/`consistency_boost` ile (ör. mevcut
config'teki 3/4 yerine 1.2/1.5 gibi) kademeli açılması, sonuçlar
gözlemlendikten sonra artırılması önerilir. **Bu görevde yapılmadı**
(varsayılan `false` bırakıldı).

---

## 13. Dynamic discovery quality filter'ının açılması

```json
"quality_ranking": { "discovery_filter_enabled": true, "discovery_min_data_quality_score": 0.3 }
```
Yalnızca §11-12 tamamlandıktan (yani TÜM belgelerde `data_quality_score`
mevcut olduktan) SONRA açılmalı — aksi halde eski index'te bu filtre
zaten no-op'tur (bkz. `app.py` yorumu), ama yine de anlamlı bir filtreleme
sağlaması için backfill'in bitmiş olması gerekir. **Bu görevde yapılmadı**
(varsayılan `false` bırakıldı).

---

## 14. Rollback

- §11 (search config geçişi) tek bir JSON değişikliği + `git revert` +
  redeploy ile anında geri alınabilir — eski index'ler silinmediği sürece
  (§15) `search_indices`'i eski değerlere döndürmek yeterlidir.
- §12/§13 (flag'ler) `enabled:false`/`discovery_filter_enabled:false`
  yaparak anında (kod değişikliği gerektirmeden, yalnızca config) geri
  alınabilir.
- Faz 2 (kalite backfill) sırasında bir sorun çıkarsa, `quality_version`
  alanı hangi belgelerin işlendiğini ayırt etmeye yarar — yeniden
  çalıştırma güvenlidir (idempotent `update` işlemi).

---

## 15. Eski index'leri hemen silmeme

`amazon-products-000001`/`000002` (ve autocomplete eşleri), §11-14 tam
olarak doğrulanıp production'da bir süre (öneri: en az 1-2 hafta) sorunsuz
çalıştıktan sonra, **kullanıcı onayıyla** silinmelidir. Bu görev kapsamında
hiçbir silme işlemi yapılmadı ve önerilmedi.

---

## Özet — bu görevde gerçekten çalıştırılan Elasticsearch işlemleri

```text
GET amazon-products-000001,amazon-products-000002/_mapping
GET amazon-products-000001,amazon-products-000002/_count
GET amazon-products-autocomplete-000001,amazon-products-autocomplete-000002/_mapping
GET amazon-products-autocomplete-000001,amazon-products-autocomplete-000002/_count
GET amazon-products-000001,amazon-products-000002/_settings
GET amazon-products-000001,amazon-products-000002/_stats/store
```

Hiçbir `PUT`, `POST .../_reindex`, `POST .../_update`, `POST .../_bulk`,
alias değişikliği veya silme işlemi çalıştırılmadı.
