"""
`product_quality.py` testleri. Elasticsearch'e HİÇBİR istek atılmaz — modül
zaten ES'e bağlanmaz (bkz. product_quality.py docstring).
"""

import json

import pytest

import product_quality as pq


MISMATCH_FLAG = "title_category_mismatch"


def _product(title="", categories=None, main_category="", categories_text=None, **extra):
    body = {
        "title": title,
        "main_category": main_category,
        "categories": categories if categories is not None else [],
    }
    if categories_text is not None:
        body["categories_text"] = categories_text
    else:
        body["categories_text"] = " ".join(categories or ([main_category] if main_category else []))
    body.update(extra)
    return body


# ---------------------------------------------------------------------------
# Tutarlı (consistent) kombinasyonlar — mismatch flag'i ALMAMALI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "title,categories_text",
    [
        ("Gaming Mouse Black", "Computers Computer Accessories Mice"),
        ("Makeup Brush Set", "Beauty Makeup"),
        ("Car Phone Holder", "Automotive"),
        ("Mouse Pad", "Computer Accessories"),
        ("Book Light", "Books"),
        ("Pet Hair Vacuum", "Pet Supplies"),
    ],
)
def test_consistent_combinations_do_not_flag_mismatch(title, categories_text):
    result = pq.evaluate_product_quality(_product(title=title, categories_text=categories_text))
    assert MISMATCH_FLAG not in result["quality_flags"]
    assert result["title_category_consistency"] >= 0.35


# ---------------------------------------------------------------------------
# Tutarsız (inconsistent) kombinasyonlar — mismatch flag'i ALMALI, düşük skor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "title,categories_text",
    [
        ("Gaming Mouse Black", "Beauty & Personal Care Makeup Brushes"),
        ("Lipstick Set", "Computer Accessories"),
        ("Car Brake Pad", "Beauty"),
        ("Dog Food", "Office Products"),
        ("Python Programming Book", "Makeup"),
    ],
)
def test_inconsistent_combinations_flag_mismatch(title, categories_text):
    result = pq.evaluate_product_quality(_product(title=title, categories_text=categories_text))
    assert MISMATCH_FLAG in result["quality_flags"]
    assert result["title_category_consistency"] < 0.35


# ---------------------------------------------------------------------------
# False-positive korumaları — bariz eşleşmeyen ama meşru çok-alanlı ürünler
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "title,categories_text",
    [
        ("Harry Potter Watch", "Books Collectibles"),
        ("Apple Watch Band", "Wearable Technology Accessories Fashion"),
        ("Beauty Camera Filter", "Camera & Photo"),
        ("Gaming Chair", "Furniture"),
        ("Book Light", "Lighting"),
        ("Pet Hair Vacuum", "Home Appliances"),
    ],
)
def test_false_positive_protections_do_not_flag_mismatch(title, categories_text):
    result = pq.evaluate_product_quality(_product(title=title, categories_text=categories_text))
    assert MISMATCH_FLAG not in result["quality_flags"], result


# ---------------------------------------------------------------------------
# Taxonomy kalibrasyonu: "mouse" — bare kelime elektronik sinyali ÜRETMEMELİ,
# yalnızca bağlamsal ifadeler ("gaming mouse", "wireless mouse", ...) üretmeli.
# Gerçek production offline örneklemesinde ("gaming mouse" sorgusu, %40
# mismatch oranı) bare "mouse"un "Mickey Mouse"/"Minnie Mouse"/"Disney"
# ürünlerini yanlışlıkla electronics_computers'a atadığı tespit edildi.
# ---------------------------------------------------------------------------

def test_bare_mouse_alone_does_not_signal_electronics():
    taxonomy = pq.load_category_taxonomy()
    cfg = pq.load_quality_config()
    scores = pq._family_scores(pq.normalize_text("mouse"), taxonomy, "title", cfg)
    assert "electronics_computers" not in scores


def test_bare_mouse_title_does_not_trigger_mismatch_against_unrelated_category():
    result = pq.evaluate_product_quality(_product(title="Mouse", categories_text="Beauty & Personal Care Makeup"))
    assert MISMATCH_FLAG not in result["quality_flags"]


@pytest.mark.parametrize(
    "title",
    [
        "Wireless Mouse",
        "Bluetooth Mouse",
        "Computer Mouse",
        "Gaming Mouse",
        "Optical Mouse",
        "Mouse Pad",
    ],
)
def test_contextual_mouse_phrases_still_signal_electronics(title):
    # Bağlamsal ifadeler hâlâ güçlü electronics sinyali üretmeli — bunu
    # kanıtlamak için bilerek çelişen bir kategoriyle eşleştirilip mismatch
    # flag'inin hâlâ tetiklendiği doğrulanıyor.
    result = pq.evaluate_product_quality(
        _product(title=title, categories_text="Beauty & Personal Care Makeup"), include_explanation=True
    )
    assert result["quality_explanation"]["signals"]["title_family"] == "electronics_computers"
    assert MISMATCH_FLAG in result["quality_flags"]


@pytest.mark.parametrize(
    "title,categories_text",
    [
        ("Disney Baby Mickey Mouse Hair Brush & Comb Set (Blue)", "Beauty & Personal Care Hair Care Styling Tools & Appliances Hair Brushes"),
        ("Women's Mickey and Minnie Mouse Super Minky Plush 2-Piece Pajama Set", "Clothing Shoes & Jewelry"),
        ("Mickey Mouse Locket Plate Floating Charm", "Arts, Crafts & Sewing Beading & Jewelry Making Charms"),
        ("Invicta Men's Disney Limited Edition Mickey Mouse Automatic Watch, 27409", "Watches"),
        ("Crazy Girls Ladies Long Sleeve Mickey Mouse Batman Print Baggy Jumper Sweatshirt", "Clothing Shoes & Jewelry"),
        ("YanJie Lovely Sequin Mouse Ears Clip- Glitter Hair Accessories Party Favor Decoration", "Beauty & Personal Care Hair Care Hair Accessories Clips & Barrettes Clips"),
    ],
)
def test_mickey_minnie_disney_mouse_products_do_not_false_positive(title, categories_text):
    # Gerçek production katalogundan alınmış, offline örneklemede yanlışlıkla
    # title_category_mismatch alan gerçek ürün başlıkları.
    result = pq.evaluate_product_quality(_product(title=title, categories_text=categories_text), include_explanation=True)
    assert MISMATCH_FLAG not in result["quality_flags"], result
    assert result["quality_explanation"]["signals"]["title_family"] != "electronics_computers"


# ---------------------------------------------------------------------------
# Taxonomy kalibrasyonu v3: 6.500 ürünlük (5.000 rastgele + 15x100 sorgu)
# canlı örneklemede title_category_mismatch flag'i alan 85 üründen 55'inin
# (%65) kök nedeni beş genel/tekrarlayan (tek ürüne özel OLMAYAN) taksonomi
# sorunuydu — bkz. product_quality.py yerine config/category_taxonomy.json
# değişiklikleri. Her biri için pozitif (sinyal hâlâ çalışıyor) ve negatif
# (yanlış flag artık gelmiyor) test eklenmiştir.
# ---------------------------------------------------------------------------

def test_bare_fashion_department_alone_does_not_signal_clothing():
    # main_category="AMAZON FASHION" gibi salt departman adı (detaylı
    # categories breadcrumb'ı boşken) çok zayıf/güvenilmez bir sinyaldi —
    # 23/85 mismatch bu tek desenden geliyordu (bkz. "Blue Light Blocking
    # Computer Glasses" gibi ürünler, AMAZON FASHION departmanına dahil ama
    # elektronik değil, giyim de değil).
    taxonomy = pq.load_category_taxonomy()
    cfg = pq.load_quality_config()
    scores = pq._family_scores(pq.normalize_text("amazon fashion"), taxonomy, "category", cfg)
    assert "clothing_fashion" not in scores


def test_bare_fashion_department_no_longer_flags_mismatch():
    result = pq.evaluate_product_quality(
        _product(
            title="Blue Light Blocking Computer Glasses",
            categories=[],
            main_category="AMAZON FASHION",
            categories_text="AMAZON FASHION Amazon_Fashion",
        )
    )
    assert MISMATCH_FLAG not in result["quality_flags"]


def test_real_clothing_breadcrumb_still_signals_clothing_fashion():
    result = pq.evaluate_product_quality(
        _product(
            title="Leather Jacket",
            categories_text="Clothing, Shoes & Jewelry | Clothing | Coats & Jackets",
        ),
        include_explanation=True,
    )
    assert result["quality_explanation"]["signals"]["category_family"] == "clothing_fashion"


def test_motorcycle_apparel_under_automotive_no_longer_flags_mismatch():
    # Motosiklet/otomotiv koruyucu giysileri (ceket, pantolon, çorap...)
    # meşru biçimde Automotive kategorisinde ama başlıkta jenerik giyim
    # kelimeleri geçiyor — 16/85 mismatch bu iki ailenin (clothing_fashion /
    # automotive) gereksiz yere "conflicting" işaretlenmesinden geliyordu.
    result = pq.evaluate_product_quality(
        _product(
            title="Maiyu Motorcycle Rain Suit Waterproof Rain Jacket and Pants",
            categories_text="Automotive | Motorcycle & Powersports | Protective Gear | Rainwear | Rain Jackets",
        )
    )
    assert MISMATCH_FLAG not in result["quality_flags"]


def test_clothing_fashion_still_conflicts_with_tools_home_improvement():
    # automotive kaldırıldı ama clothing_fashion'ın DİĞER conflict'leri
    # (ör. tools_home_improvement) hâlâ aktif olmalı.
    result = pq.evaluate_product_quality(
        _product(title="Power Drill Wrench Tool Set", categories_text="Clothing, Shoes & Jewelry")
    )
    assert MISMATCH_FLAG in result["quality_flags"]


def test_car_vacuum_cleaner_under_automotive_no_longer_flags_mismatch():
    # "vacuum cleaner"/"light bulb" gibi home_kitchen ifadeleri, araca özel
    # (Car Care | Vacuums) ürünlerde de meşru biçimde geçiyor — 8/85 mismatch
    # home_kitchen'ın TEK conflict'i olan automotive'den geliyordu.
    result = pq.evaluate_product_quality(
        _product(
            title="Portable Car Vacuum Cleaner Handheld",
            categories_text="Automotive | Car Care | Interior Care | Vacuums",
        )
    )
    assert MISMATCH_FLAG not in result["quality_flags"]


def test_home_kitchen_legitimate_match_still_scores_high():
    result = pq.evaluate_product_quality(
        _product(title="Cordless Vacuum Cleaner", categories_text="Home & Kitchen | Vacuums & Floor Care")
    )
    assert MISMATCH_FLAG not in result["quality_flags"]
    assert result["title_category_consistency"] >= 0.7


def test_bare_pc_as_piece_abbreviation_no_longer_signals_electronics():
    # Amazon başlıklarında "1 Pc"/"6 pc Set" gibi kullanımlar "personal
    # computer" değil "piece" anlamına gelir — bare "pc" 5/85 mismatch'in
    # kaynağıydı (ör. "Trim Tweezing Mirror ... 1 Pc", Beauty kategorisinde).
    taxonomy = pq.load_category_taxonomy()
    cfg = pq.load_quality_config()
    scores = pq._family_scores(pq.normalize_text("1 Pc Wax Pen and 1 Pc Brush Pen"), taxonomy, "title", cfg)
    assert "electronics_computers" not in scores


def test_bare_pc_piece_abbreviation_no_longer_flags_mismatch():
    result = pq.evaluate_product_quality(
        _product(
            title="Trim Tweezing Mirror 10x Magnifying. 1 Pc",
            categories_text="Beauty & Personal Care | Tools & Accessories | Mirrors",
        )
    )
    assert MISMATCH_FLAG not in result["quality_flags"]


@pytest.mark.parametrize("title", ["Desktop PC Tower Gaming Computer", "Gaming PC Prebuilt Tower"])
def test_pc_phrases_still_signal_electronics(title):
    taxonomy = pq.load_category_taxonomy()
    cfg = pq.load_quality_config()
    scores = pq._family_scores(pq.normalize_text(title), taxonomy, "title", cfg)
    assert "electronics_computers" in scores


def test_bare_conditioner_as_air_conditioner_no_longer_signals_beauty():
    # "conditioner" bare hem "hair conditioner" hem "air conditioner" ile
    # eşleşiyordu — 3/85 mismatch otomotiv Air Conditioner parçalarından
    # geliyordu. Artık yalnızca "hair conditioner" phrase'i sinyal üretir.
    taxonomy = pq.load_category_taxonomy()
    cfg = pq.load_quality_config()
    scores = pq._family_scores(pq.normalize_text("Air Conditioner Compressor Control Relay"), taxonomy, "title", cfg)
    assert "beauty_personal_care" not in scores


def test_air_conditioner_part_no_longer_flags_mismatch():
    result = pq.evaluate_product_quality(
        _product(
            title="Airtex 1R1057 Air Conditioner Compressor Control Relay",
            categories_text="Automotive | Replacement Parts | Switches & Relays | Relays | Air Conditioning",
        )
    )
    assert MISMATCH_FLAG not in result["quality_flags"]


def test_hair_conditioner_phrase_still_signals_beauty():
    result = pq.evaluate_product_quality(
        _product(title="Moisturizing Hair Conditioner 500ml", categories_text="Beauty & Personal Care | Hair Care")
    )
    assert MISMATCH_FLAG not in result["quality_flags"]
    assert result["title_category_consistency"] >= 0.7


# ---------------------------------------------------------------------------
# Eksik / bozuk veri
# ---------------------------------------------------------------------------

def test_empty_title_does_not_crash():
    result = pq.evaluate_product_quality(_product(title="", categories_text="Electronics"))
    assert "missing_title" in result["quality_flags"]
    assert 0.0 <= result["data_quality_score"] <= 1.0


def test_empty_categories_does_not_crash():
    result = pq.evaluate_product_quality({"title": "Gaming Mouse", "categories": [], "categories_text": ""})
    assert "missing_categories" in result["quality_flags"]


def test_both_title_and_categories_empty():
    result = pq.evaluate_product_quality({"title": "", "categories": [], "categories_text": ""})
    assert "missing_title_and_categories" in result["quality_flags"]
    assert "missing_title" not in result["quality_flags"]
    assert "missing_categories" not in result["quality_flags"]


def test_malformed_categories_type_does_not_raise():
    result = pq.evaluate_product_quality({"title": "Gaming Mouse", "categories": {"unexpected": "shape"}})
    assert result["quality_flags"] != ["quality_evaluation_failed"]
    assert "missing_categories" in result["quality_flags"]


def test_missing_rating_and_price_are_not_penalized():
    result = pq.evaluate_product_quality(_product(title="Gaming Mouse", categories_text="Electronics"))
    assert "invalid_rating" not in result["quality_flags"]
    assert "invalid_price" not in result["quality_flags"]


def test_low_rating_and_price_are_not_penalized():
    result = pq.evaluate_product_quality(
        _product(title="Gaming Mouse", categories_text="Electronics", average_rating=1.0, price=0.5, rating_number=1)
    )
    assert "invalid_rating" not in result["quality_flags"]
    assert "invalid_price" not in result["quality_flags"]
    assert "invalid_rating_number" not in result["quality_flags"]


def test_invalid_rating_out_of_range_is_flagged():
    result = pq.evaluate_product_quality(_product(title="Gaming Mouse", categories_text="Electronics", average_rating=99))
    assert "invalid_rating" in result["quality_flags"]


def test_invalid_negative_price_is_flagged():
    result = pq.evaluate_product_quality(_product(title="Gaming Mouse", categories_text="Electronics", price=-5))
    assert "invalid_price" in result["quality_flags"]


def test_invalid_negative_rating_number_is_flagged():
    result = pq.evaluate_product_quality(
        _product(title="Gaming Mouse", categories_text="Electronics", rating_number=-3)
    )
    assert "invalid_rating_number" in result["quality_flags"]


def test_quality_evaluator_exception_returns_safe_fallback(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(pq, "_evaluate", boom)
    result = pq.evaluate_product_quality({"title": "Gaming Mouse"})
    assert result == {
        "title_category_consistency": 0.5,
        "data_quality_score": 0.5,
        "quality_flags": ["quality_evaluation_failed"],
        "quality_version": pq.QUALITY_VERSION,
    }


def test_non_dict_product_does_not_raise():
    result = pq.evaluate_product_quality(None)
    assert result["quality_flags"]


# ---------------------------------------------------------------------------
# Genel sözleşme / şema
# ---------------------------------------------------------------------------

def test_result_has_required_production_fields():
    result = pq.evaluate_product_quality(_product(title="Gaming Mouse", categories_text="Electronics"))
    assert set(["title_category_consistency", "data_quality_score", "quality_flags", "quality_version"]) <= set(
        result.keys()
    )
    assert isinstance(result["title_category_consistency"], float)
    assert isinstance(result["data_quality_score"], float)
    assert isinstance(result["quality_flags"], list)
    assert 0.0 <= result["title_category_consistency"] <= 1.0
    assert 0.0 <= result["data_quality_score"] <= 1.0


def test_explanation_is_excluded_by_default():
    result = pq.evaluate_product_quality(_product(title="Gaming Mouse", categories_text="Electronics"))
    assert "quality_explanation" not in result


def test_explanation_can_be_requested():
    result = pq.evaluate_product_quality(
        _product(title="Gaming Mouse", categories_text="Electronics"), include_explanation=True
    )
    assert "quality_explanation" in result
    assert "signals" in result["quality_explanation"]


def test_exact_asin_field_is_not_referenced_by_quality_module():
    # evaluate_product_quality bir arama sorgusu değildir; ASIN'e dokunmaz/
    # değiştirmez, yalnızca `parent_asin`in VARLIĞINI tamlık sinyali olarak kullanır.
    with_asin = pq.evaluate_product_quality(
        _product(title="Gaming Mouse", categories_text="Electronics", parent_asin="B000123456")
    )
    without_asin = pq.evaluate_product_quality(_product(title="Gaming Mouse", categories_text="Electronics"))
    assert "missing_asin" not in with_asin["quality_flags"]
    assert "missing_asin" in without_asin["quality_flags"]


# ---------------------------------------------------------------------------
# Normalizasyon
# ---------------------------------------------------------------------------

def test_normalize_text_handles_unicode_and_case():
    assert pq.normalize_text("Gaming-Mouse!!") == "gaming mouse"
    assert pq.normalize_text(None) == ""
    assert pq.normalize_text("") == ""


def test_normalize_text_does_not_apply_turkish_specific_casing():
    # "I" -> Türkçe kurala göre "ı" olurdu; burada dil-bağımsız casefold
    # kullanıldığı için model kodları (ör. "IP68") bozulmamalı.
    assert "ı" not in pq.normalize_text("IP68")


# ---------------------------------------------------------------------------
# Taxonomy / config doğrulama
# ---------------------------------------------------------------------------

def test_default_taxonomy_and_quality_config_load_successfully():
    taxonomy = pq.load_category_taxonomy()
    cfg = pq.load_quality_config()
    assert taxonomy.families
    assert cfg.quality_version == "v1"


def test_taxonomy_unknown_conflicting_family_is_rejected(tmp_path):
    data = {
        "families": {
            "a": {"title_terms": ["x"], "category_terms": ["y"], "conflicting_families": ["does_not_exist"]},
        }
    }
    path = tmp_path / "category_taxonomy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(pq.QualityConfigError):
        pq.load_category_taxonomy(path)


def test_taxonomy_empty_families_is_rejected(tmp_path):
    path = tmp_path / "category_taxonomy.json"
    path.write_text(json.dumps({"families": {}}), encoding="utf-8")
    with pytest.raises(pq.QualityConfigError):
        pq.load_category_taxonomy(path)


def test_quality_config_missing_file_is_rejected(tmp_path):
    with pytest.raises(pq.QualityConfigError):
        pq.load_quality_config(tmp_path / "does-not-exist.json")


def test_quality_config_weight_sum_must_equal_one(tmp_path):
    import copy

    raw = json.loads(pq.QUALITY_CONFIG_PATH.read_text(encoding="utf-8"))
    raw = copy.deepcopy(raw)
    raw["weights"]["data_quality"]["consistency"] = 0.9
    path = tmp_path / "quality_config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(pq.QualityConfigError):
        pq.load_quality_config(path)
