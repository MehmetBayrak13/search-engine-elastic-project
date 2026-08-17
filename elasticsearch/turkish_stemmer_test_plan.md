# Türkçe Stemmer — Test Index ve Analyzer Hazırlığı

> **SONUÇ (sonraki bir oturumda çalıştırıldı):** Bu dosyanın önerdiği genel
> `turkish` stemmer yaklaşımı test edildi ve **reddedildi** — canlı
> cluster'da ölçüldü: en yaygın 500 markadan 137'si (%27), sıradan İngilizce
> ürün-açıklama kelimelerinin de ~%30'u genel stemmer tarafından anlamsızca
> kırpılıyordu (`iPhone→ipho`, `Nike→nik`, `compatible→compatip`,
> `electric→electri` gibi) — üstelik katalog neredeyse tamamen İngilizce
> olduğu için (5.9M üründen yalnızca 8'inde gerçek Türkçe metin bulundu),
> genel stemmer'ın getirisi neredeyse sıfırdı. Bunun yerine, **yalnızca
> `config/query_translations.json`daki bilinen ~550 Türkçe kelime için**
> `product_stem_override` kurallarını programatik olarak (çoğul/iyelik ekleri,
> ünsüz yumuşaması dahil) genişleten, genel stemmer'sız bir yaklaşım
> uygulandı — İngilizce metne SIFIR risk (override yalnızca tam eşleşen
> kelimeyi değiştirir, algoritmik kırpma yapmaz). Reindex edildi:
> `amazon-products-000003`/`-000004` (bkz. README "Elasticsearch indexleri").
> Aşağıdaki içerik, o kararın öncesindeki orijinal hazırlık planı olarak
> tarihsel referans için bırakılmıştır.

Bu dosya **yalnızca hazırlıktır**. Aşağıdaki komutlardan hiçbiri bu görev
kapsamında çalıştırılmadı:

- Production index'e (`amazon-products-000001`, `amazon-products-000002`,
  `amazon-products-autocomplete-000001`, `amazon-products-autocomplete-000002`)
  **hiçbir değişiklik yapılmadı.**
- Hiçbir reindex başlatılmadı.
- Hiçbir index oluşturulmadı/silinmedi.

Komutlar, Kibana **Dev Tools**'a yapıştırıp kullanıcının kendisinin
çalıştırması için hazırlanmıştır. Bu ortamda canlı bir Elastic Cloud
bağlantısı/kimlik bilgisi kullanılmadı — hiçbir komut fiilen çalıştırılmadı.

## Neden query translation stemming problemini çözmez

`config/query_translations.json` + `app.py:expand_multilingual_query`,
**Türkçe → İngilizce anlam çevirisi** yapar (ör. `kulaklık` → `headphones`).
Bu, veri seti İngilizce olduğu için gereklidir ve sözlük eşleşmesine
dayanır — sözlükte olmayan hiçbir kelimeyi çözemez.

**Türkçe morphology (çekim ekleri) problemi** bambaşkadır: `telefon`,
`telefonlar`, `telefonların`, `telefonu` gibi biçimlerin **aynı Türkçe
kelimenin** çekimli hâlleri olduğunu tanımak için gerekir — çeviri değil,
**stemming** (kök bulma) gerektirir. Bunu tek tek sözlüğe yazmak
sürdürülemez; bu yüzden Elasticsearch'ün genel `turkish` stemmer'ı
kullanılmalıdır (bkz. aşağıdaki analyzer).

## 1. Test index'i oluştur (Kibana Dev Tools)

```http
PUT amazon-products-tr-stemmer-test-000001
{
  "settings": {
    "analysis": {
      "filter": {
        "turkish_lowercase": {
          "type": "lowercase",
          "language": "turkish"
        },
        "turkish_stemmer": {
          "type": "stemmer",
          "language": "turkish"
        },
        "product_stemmer_override": {
          "type": "stemmer_override",
          "rules": [
            "iphone => iphone",
            "iphone'un => iphone",
            "iphone'a => iphone"
          ]
        }
      },
      "analyzer": {
        "turkish_product_analyzer_test": {
          "type": "custom",
          "tokenizer": "standard",
          "filter": [
            "apostrophe",
            "turkish_lowercase",
            "product_stemmer_override",
            "turkish_stemmer"
          ]
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "parent_asin": { "type": "keyword" },
      "title": {
        "type": "text",
        "fields": {
          "keyword": { "type": "keyword", "ignore_above": 256 },
          "tr": { "type": "text", "analyzer": "turkish_product_analyzer_test" }
        }
      },
      "categories_text": {
        "type": "text",
        "fields": {
          "tr": { "type": "text", "analyzer": "turkish_product_analyzer_test" }
        }
      },
      "description": {
        "type": "text",
        "fields": {
          "tr": { "type": "text", "analyzer": "turkish_product_analyzer_test" }
        }
      },
      "features": {
        "type": "text",
        "fields": {
          "tr": { "type": "text", "analyzer": "turkish_product_analyzer_test" }
        }
      }
    }
  }
}
```

**Filter sırası (istenen sıraya göre):**

1. `apostrophe` — yerleşik (built-in) filtre; kesme işaretinden sonrasını atar
   (Türkçe iyelik ekleri için, ör. `iPhone'un` → `iPhone`).
2. `turkish_lowercase` — `lowercase` filtresinin `language: turkish` varyantı
   (Türkçe'ye özgü büyük/küçük harf kurallarını doğru uygular: `İ` → `i`,
   `I` → `ı`).
3. `product_stemmer_override` — **yalnızca gerçek istisnalar için**. Normal
   Türkçe çekim eklerini (`-lar`, `-ların`, `-da`, `-dan`, `-u` vb.) burada
   TEK TEK LİSTELEMİYORUZ; bu kural sadece marka/model gibi genel stemmer'ın
   yanlış kırptığı özel durumları düzeltmek içindir. Başlangıçta minimal
   tutulmuştur (yalnızca örnek `iphone` kuralları) — gerçek `_analyze`
   sonuçlarına göre genişletilmeli/daraltılmalı.
4. `turkish_stemmer` — Elasticsearch'ün **genel** `stemmer` filtresi,
   `language: turkish`. Türkçe çekim eklerinin büyük çoğunluğunu burada,
   kural yazılmadan otomatik ele alır.

Bu analyzer **yeni bir izole test index'inde** tanımlıdır; mevcut production
mapping'lerine veya autocomplete Edge NGram analyzer'ına dokunmaz.

## 2. `_analyze` testleri

Her grup için ayrı bir istek — aynı kelime grubundaki tüm biçimlerin aynı
(veya tutarlı) köke indirgenip indirgenmediğini görmek için `text` alanına
bir liste verilir.

```http
POST amazon-products-tr-stemmer-test-000001/_analyze
{
  "analyzer": "turkish_product_analyzer_test",
  "text": ["telefon", "telefonlar", "telefonların", "telefonlarda", "telefonlardan", "telefonu"]
}
```

```http
POST amazon-products-tr-stemmer-test-000001/_analyze
{
  "analyzer": "turkish_product_analyzer_test",
  "text": ["kitap", "kitaplar", "kitapların", "kitaplarda"]
}
```

```http
POST amazon-products-tr-stemmer-test-000001/_analyze
{
  "analyzer": "turkish_product_analyzer_test",
  "text": ["kulaklık", "kulaklıklar", "kulaklıkların"]
}
```

```http
POST amazon-products-tr-stemmer-test-000001/_analyze
{
  "analyzer": "turkish_product_analyzer_test",
  "text": ["araba", "arabalar", "arabaları"]
}
```

```http
POST amazon-products-tr-stemmer-test-000001/_analyze
{
  "analyzer": "turkish_product_analyzer_test",
  "text": ["şarj", "şarjlı", "şarjın"]
}
```

Marka/model/ASIN — bunların **zarar görmemesi** gerekir (stemmer'ın anlamsız
şekilde kırpmaması):

```http
POST amazon-products-tr-stemmer-test-000001/_analyze
{
  "analyzer": "turkish_product_analyzer_test",
  "text": ["Apple", "Samsung", "iPhone", "Galaxy", "S24"]
}
```

```http
POST amazon-products-tr-stemmer-test-000001/_analyze
{
  "analyzer": "turkish_product_analyzer_test",
  "text": ["B092LWRHRH"]
}
```

Referans için, `standard` tokenizer + yalnızca `turkish_lowercase` (stemmer'sız)
çıktısını da almak isterseniz karşılaştırma amaçlı:

```http
POST amazon-products-tr-stemmer-test-000001/_analyze
{
  "tokenizer": "standard",
  "filter": ["apostrophe", "turkish_lowercase"],
  "text": ["telefonların", "kitapların", "B092LWRHRH", "iPhone"]
}
```

## 3. Değerlendirme kriterleri

Sonuçları şu sorularla değerlendirin (token değişti diye otomatik olarak
"doğru" sayılmaz):

| Kontrol | Beklenen |
|---|---|
| `telefon / telefonlar / telefonların / telefonlarda / telefonlardan / telefonu` | Hepsi aynı (veya arama açısından eşdeğer) tek bir token'a inmeli |
| `kitap` grubu | Aynı şekilde tek köke inmeli |
| `kulaklık` grubu | Aynı şekilde tek köke inmeli |
| `araba` grubu | Aynı şekilde tek köke inmeli |
| `şarj` grubu | Aynı şekilde tek köke inmeli (`şarj` teknik terim olduğundan kontrol edilmeli — anlamsız kırpma varsa `product_stemmer_override`'a eklenmeli) |
| `Apple`, `Samsung`, `Galaxy` | Yalnızca lowercase olmalı, kök bulma ile **değiştirilmemeli** |
| `iPhone` | `product_stemmer_override` sayesinde `iphone` olarak sabit kalmalı |
| `S24` | Değişmemeli (alfanumerik model kodu) |
| `B092LWRHRH` (ASIN) | Tek token, yalnızca lowercase edilmiş hâliyle (`b092lwrhrh`) kalmalı — bölünmemeli/kırpılmamalı |

Eğer bir marka/model/ASIN stemmer tarafından anlamsız şekilde kırpılıyorsa,
`product_stemmer_override.rules` listesine yalnızca o **spesifik** terim
eklenir — normal Türkçe çekim ekleri asla buraya elle yazılmaz (bu, tam
olarak projenin kaçınmak istediği "elle sözlük" yaklaşımıdır).

## 4. Bu noktadan sonra (kullanıcı onayı olmadan yapılmayacaklar)

Test sonuçları memnun edici bulunursa, production'a taşımak için (bu görev
kapsamında **yapılmadı**, yalnızca sıralama bilgisi amaçlıdır):

1. Yeni bir production mapping'i (`amazon-products-000003` gibi) test
   sonuçlarına göre nihai hâliyle tasarlanır.
2. Küçük bir örneklem reindex edilip karşılaştırılır.
3. Depolama/süre tahmini çıkarılır.
4. Tam reindex komutu **kullanıcıya sunulur**, açık onay beklenir.
5. Onay sonrası `_reindex` (asenkron, throttled, izlenebilir) ile taşınır.
6. Belge sayısı karşılaştırılır, alias cutover yapılır.
7. Eski index otomatik silinmez.

Bu adımların hiçbiri bu görevde çalıştırılmadı.
