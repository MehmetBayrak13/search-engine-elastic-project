import os
import sys
from collections.abc import Iterator
from typing import Any

os.environ["HF_HUB_DISABLE_XET"] = "1"

from datasets import load_dataset
from elasticsearch import Elasticsearch, helpers
from huggingface_hub import HfApi

from product_quality import QUALITY_VERSION, evaluate_product_quality


INDEX_NAME = "amazon-products-v1"
PRODUCTS_PER_CATEGORY = 10

REPO_ID = "McAuley-Lab/Amazon-Reviews-2023"
DATASET_REVISION = "6a62d7bf8b3f3a90943ac9c6ea800ae7736959ad"

CATEGORIES = [
    "All_Beauty",
    "Appliances",
    "Automotive",
    "Books",
    "Cell_Phones_and_Accessories",
    "Clothing_Shoes_and_Jewelry",
    "Electronics",
    "Home_and_Kitchen",
    "Sports_and_Outdoors",
    "Toys_and_Games",
]

BASE_URL = (
    "https://huggingface.co/datasets/"
    f"{REPO_ID}/resolve/{DATASET_REVISION}"
)


def discover_category_files() -> dict[str, tuple[str, str]]:
    """
    Sabitlenmiş dataset revision içindeki gerçek raw_meta klasörlerini tarar.
    Her kategori için ilk Parquet parçasını bulur.
    """

    print("Hugging Face dosya listesi okunuyor...")
    print(f"Dataset revision: {DATASET_REVISION}")

    api = HfApi()
    repo_files = api.list_repo_files(
        repo_id=REPO_ID,
        repo_type="dataset",
        revision=DATASET_REVISION,
    )

    category_files: dict[str, tuple[str, str]] = {}

    for category in CATEGORIES:
        parquet_prefix = f"raw_meta_{category}/"

        parquet_files = sorted(
            path
            for path in repo_files
            if path.startswith(parquet_prefix)
            and path.endswith(".parquet")
        )

        if not parquet_files:
            print(f"UYARI: {category} için Parquet dosyası bulunamadı.")
            continue

        file_path = parquet_files[0]
        category_files[category] = (
            f"{BASE_URL}/{file_path}",
            "parquet",
        )
        print(f"{category}: {file_path} [parquet]")

    return category_files


def normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        return [
            str(item).strip()
            for item in value
            if item is not None and str(item).strip()
        ]

    text = str(value).strip()
    return [text] if text else []


def parse_price(value: Any) -> float | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if not text or text.lower() == "none":
        return None

    text = (
        text.replace("$", "")
        .replace("€", "")
        .replace("£", "")
        .replace(",", "")
        .strip()
    )

    try:
        return float(text)
    except ValueError:
        return None


def parse_integer(value: Any) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_float(value: Any) -> float | None:
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_first_image(product: dict[str, Any]) -> str | None:
    images = product.get("images") or {}

    if not isinstance(images, dict):
        return None

    for image_type in ("hi_res", "large", "thumb"):
        image_values = images.get(image_type) or []

        if not isinstance(image_values, list):
            continue

        for image_url in image_values:
            if image_url and str(image_url).strip():
                return str(image_url).strip()

    return None


def transform_product(
    product: dict[str, Any],
    source_category: str,
) -> dict[str, Any] | None:
    parent_asin = product.get("parent_asin")
    title = product.get("title")

    if not parent_asin or not title:
        return None

    document = {
        "parent_asin": str(parent_asin).strip(),
        "title": str(title).strip(),
        "main_category": str(
            product.get("main_category") or source_category
        ).strip(),
        "categories": normalize_text_list(
            product.get("categories")
        ),
        "store": str(
            product.get("store") or "unknown"
        ).strip(),
        "price": parse_price(
            product.get("price")
        ),
        "average_rating": parse_float(
            product.get("average_rating")
        ),
        "rating_number": parse_integer(
            product.get("rating_number")
        ),
        "features": normalize_text_list(
            product.get("features")
        ),
        "description": normalize_text_list(
            product.get("description")
        ),
        "image_url": get_first_image(product),
    }

    return {
        key: value
        for key, value in document.items()
        if value is not None
    }


def apply_quality_evaluation(document: dict[str, Any], category: str) -> dict[str, Any]:
    """
    `product_quality.evaluate_product_quality`yi (full_amazon_importer.py ile
    AYNI modül, kod kopyalanmadan) belgeye uygular. `evaluate_product_quality`
    kendi içinde zaten hata yutar; buradaki try/except yalnızca product_quality
    modülüyle ilgili beklenmedik bir sorunda bu basit örnek importer'ı
    durdurmamak içindir.
    """
    try:
        quality = evaluate_product_quality(document)
    except Exception as error:
        print(
            f"UYARI: {category} | {document.get('parent_asin')} için kalite "
            f"değerlendirmesi başarısız oldu: {error}"
        )
        quality = {
            "title_category_consistency": 0.5,
            "data_quality_score": 0.5,
            "quality_flags": ["quality_evaluation_failed"],
            "quality_version": QUALITY_VERSION,
        }

    document.update(quality)
    return document


def load_category_dataset(
    file_url: str,
    file_format: str,
):
    return load_dataset(
        file_format,
        data_files={"full": file_url},
        split="full",
        streaming=True,
    )


def generate_category_actions(
    category: str,
    file_url: str,
    file_format: str,
) -> Iterator[dict[str, Any]]:
    print(f"\nKategori açılıyor: {category}")
    print(f"Dosya türü: {file_format}")
    print(f"Dosya: {file_url}")

    dataset = load_category_dataset(
        file_url,
        file_format,
    )

    indexed_count = 0

    for product in dataset:
        document = transform_product(
            product,
            category,
        )

        if document is None:
            continue

        document = apply_quality_evaluation(document, category)

        yield {
            "_op_type": "index",
            "_index": INDEX_NAME,
            "_id": document["parent_asin"],
            "_source": document,
        }

        indexed_count += 1

        if indexed_count >= PRODUCTS_PER_CATEGORY:
            break


def main() -> None:
    elastic_cloud_id = os.getenv("ELASTIC_CLOUD_ID")
    elastic_api_key = os.getenv("ELASTIC_API_KEY")

    if not elastic_cloud_id:
        print("HATA: ELASTIC_CLOUD_ID ortam değişkeni tanımlı değil.")
        sys.exit(1)

    if not elastic_api_key:
        print("HATA: ELASTIC_API_KEY ortam değişkeni tanımlı değil.")
        sys.exit(1)

    client = Elasticsearch(
        cloud_id=elastic_cloud_id,
        api_key=elastic_api_key,
        request_timeout=60,
    )

    print("Elastic Cloud bağlantısı kontrol ediliyor...")

    if not client.ping():
        print("HATA: Elastic Cloud bağlantısı kurulamadı.")
        sys.exit(1)

    if not client.indices.exists(index=INDEX_NAME):
        print(f"HATA: {INDEX_NAME} indexi bulunamadı.")
        print("Önce indexi Kibana Dev Tools üzerinden oluştur.")
        sys.exit(1)

    category_files = discover_category_files()

    total_success = 0
    total_errors = 0

    for category in CATEGORIES:
        category_file = category_files.get(category)

        if not category_file:
            total_errors += 1
            continue

        file_url, file_format = category_file

        try:
            success_count, errors = helpers.bulk(
                client,
                generate_category_actions(
                    category,
                    file_url,
                    file_format,
                ),
                chunk_size=PRODUCTS_PER_CATEGORY,
                refresh=False,
                raise_on_error=False,
                request_timeout=120,
            )

            total_success += success_count
            total_errors += len(errors)

            print(
                f"{category}: "
                f"{success_count} ürün indexlendi, "
                f"{len(errors)} hata."
            )

            if errors:
                print("İlk bulk hatası:")
                print(errors[0])

        except Exception as error:
            total_errors += 1
            print(f"{category} işlenirken hata oluştu:")
            print(error)

    client.indices.refresh(index=INDEX_NAME)

    count_response = client.count(index=INDEX_NAME)

    print("\nİşlem tamamlandı.")
    print(f"Toplam başarılı işlem: {total_success}")
    print(f"Toplam hata: {total_errors}")
    print(
        "Indexteki toplam belge sayısı:",
        count_response["count"],
    )


if __name__ == "__main__":
    main()