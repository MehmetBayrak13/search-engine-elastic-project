import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

os.environ["HF_HUB_DISABLE_XET"] = "1"

from datasets import load_dataset
from elasticsearch import Elasticsearch, helpers
from huggingface_hub import HfApi


# ============================================================
# GENEL AYARLAR
# ============================================================

INDEX_ALIAS = "amazon-products"

REPO_ID = "McAuley-Lab/Amazon-Reviews-2023"

# Bütün raw_meta_* Parquet klasörlerinin bulunduğu sabit revision.
DATASET_REVISION = "6a62d7bf8b3f3a90943ac9c6ea800ae7736959ad"

BASE_URL = (
    "https://huggingface.co/datasets/"
    f"{REPO_ID}/resolve/{DATASET_REVISION}"
)

CHECKPOINT_FILE = Path("amazon_import_checkpoint.json")
ERROR_LOG_FILE = Path("import_errors.jsonl")
PROGRESS_LOG_FILE = Path("import_progress.log")

# Her Elasticsearch bulk isteğinde gönderilecek belge sayısı.
BULK_CHUNK_SIZE = 500

# Tek bulk isteğinin üst boyut sınırı.
MAX_CHUNK_BYTES = 20 * 1024 * 1024

# Geçici Elasticsearch hatalarında tekrar deneme ayarları.
MAX_RETRIES = 8
INITIAL_BACKOFF_SECONDS = 2
MAX_BACKOFF_SECONDS = 120

# Hugging Face dosyası açılırken hata oluşursa deneme sayısı.
FILE_OPEN_RETRIES = 5

# Terminalde kaç kayıtta bir ilerleme yazılacağı.
PROGRESS_INTERVAL = 5_000


# ============================================================
# LOG VE CHECKPOINT
# ============================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    line = f"[{utc_now()}] {message}"
    print(line, flush=True)

    with PROGRESS_LOG_FILE.open(
        "a",
        encoding="utf-8",
    ) as file:
        file.write(line + "\n")


def write_error(
    *,
    category: str,
    file_path: str,
    row_number: int | None,
    error: Any,
    document_id: str | None = None,
) -> None:
    record = {
        "time": utc_now(),
        "category": category,
        "file_path": file_path,
        "row_number": row_number,
        "document_id": document_id,
        "error": str(error),
    }

    with ERROR_LOG_FILE.open(
        "a",
        encoding="utf-8",
    ) as file:
        file.write(
            json.dumps(
                record,
                ensure_ascii=False,
                default=str,
            )
            + "\n"
        )


def default_checkpoint() -> dict[str, Any]:
    return {
        "revision": DATASET_REVISION,
        "completed_files": [],
        "current_file": None,
        "next_row": 0,
        "total_success": 0,
        "total_failed": 0,
        "updated_at": utc_now(),
    }


def load_checkpoint() -> dict[str, Any]:
    if not CHECKPOINT_FILE.exists():
        return default_checkpoint()

    try:
        with CHECKPOINT_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            checkpoint = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        log(
            "Checkpoint okunamadı. Yeni checkpoint oluşturuluyor. "
            f"Hata: {error}"
        )
        return default_checkpoint()

    if checkpoint.get("revision") != DATASET_REVISION:
        log(
            "Checkpoint farklı dataset revision'ına ait. "
            "Yeni checkpoint oluşturuluyor."
        )
        return default_checkpoint()

    checkpoint.setdefault("completed_files", [])
    checkpoint.setdefault("current_file", None)
    checkpoint.setdefault("next_row", 0)
    checkpoint.setdefault("total_success", 0)
    checkpoint.setdefault("total_failed", 0)

    return checkpoint


def save_checkpoint(checkpoint: dict[str, Any]) -> None:
    checkpoint["updated_at"] = utc_now()

    temporary_file = CHECKPOINT_FILE.with_suffix(".tmp")

    with temporary_file.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            checkpoint,
            file,
            ensure_ascii=False,
            indent=2,
        )

    temporary_file.replace(CHECKPOINT_FILE)


# ============================================================
# AMAZON DOSYALARINI KEŞFETME
# ============================================================

def category_from_file_path(file_path: str) -> str:
    folder_name = file_path.split("/", maxsplit=1)[0]

    if folder_name.startswith("raw_meta_"):
        return folder_name.removeprefix("raw_meta_")

    return folder_name


def discover_all_metadata_files() -> list[tuple[str, str]]:
    """
    Dataset deposundaki bütün raw_meta_* Parquet parçalarını bulur.

    Dönen değer:
        [
            ("All_Beauty", "raw_meta_All_Beauty/full-....parquet"),
            ...
        ]
    """

    log("Hugging Face deposundaki metadata dosyaları aranıyor...")

    api = HfApi()

    repo_files = api.list_repo_files(
        repo_id=REPO_ID,
        repo_type="dataset",
        revision=DATASET_REVISION,
    )

    metadata_files: list[tuple[str, str]] = []

    for file_path in repo_files:
        if not file_path.startswith("raw_meta_"):
            continue

        if not file_path.endswith(".parquet"):
            continue

        category = category_from_file_path(file_path)
        metadata_files.append((category, file_path))

    metadata_files.sort(
        key=lambda item: (
            item[0].lower(),
            item[1].lower(),
        )
    )

    if not metadata_files:
        raise RuntimeError(
            "Hiç raw_meta_* Parquet dosyası bulunamadı."
        )

    category_counts: dict[str, int] = defaultdict(int)

    for category, _ in metadata_files:
        category_counts[category] += 1

    log(
        f"{len(category_counts)} kategori ve "
        f"{len(metadata_files)} Parquet parçası bulundu."
    )

    for category in sorted(category_counts):
        log(
            f"Kategori keşfedildi: {category} "
            f"({category_counts[category]} dosya)"
        )

    return metadata_files


def build_file_url(file_path: str) -> str:
    return f"{BASE_URL}/{file_path}"


# ============================================================
# ALAN DÖNÜŞÜMLERİ
# ============================================================

def clean_text(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    return text


def normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, (list, tuple)):
        result: list[str] = []

        for item in value:
            text = clean_text(item)

            if text:
                result.append(text)

        return result

    text = clean_text(value)
    return [text] if text else []


def parse_price(value: Any) -> float | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        parsed = float(value)

        if parsed < 0:
            return None

        return parsed

    text = str(value).strip()

    if not text or text.lower() in {
        "none",
        "null",
        "nan",
        "n/a",
    }:
        return None

    text = (
        text.replace("$", "")
        .replace("€", "")
        .replace("£", "")
        .replace(",", "")
        .strip()
    )

    try:
        parsed = float(text)
    except ValueError:
        return None

    if parsed < 0:
        return None

    return parsed


def parse_integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None

    if parsed < 0:
        return None

    return parsed


def parse_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def get_first_image(product: dict[str, Any]) -> str | None:
    images = product.get("images") or {}

    if not isinstance(images, dict):
        return None

    for image_type in (
        "hi_res",
        "large",
        "thumb",
    ):
        image_values = images.get(image_type) or []

        if not isinstance(image_values, (list, tuple)):
            continue

        for image_url in image_values:
            cleaned_url = clean_text(image_url)

            if cleaned_url:
                return cleaned_url

    return None


def transform_product(
    product: dict[str, Any],
    source_category: str,
) -> dict[str, Any] | None:
    """
    Bütün Amazon kategorilerinde kullanılabilecek ortak dönüşüm.

    Telefon, televizyon, kitap veya başka bir ürün türüne özel
    hiçbir if/else kuralı içermez.
    """

    parent_asin = clean_text(
        product.get("parent_asin")
    )
    title = clean_text(
        product.get("title")
    )

    if not parent_asin or not title:
        return None

    categories = normalize_text_list(
        product.get("categories")
    )

    main_category = clean_text(
        product.get("main_category")
    )

    if not main_category:
        main_category = source_category.replace("_", " ")

    store = clean_text(
        product.get("store")
    )

    if not store:
        store = "unknown"

    document: dict[str, Any] = {
        "parent_asin": parent_asin,
        "title": title,
        "main_category": main_category,
        "categories": categories,
        "categories_text": " ".join(categories),
        "source_category": source_category,
        "store": store,
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


# ============================================================
# DATASET OKUMA
# ============================================================

def open_parquet_dataset(
    file_url: str,
    category: str,
    file_path: str,
):
    last_error: Exception | None = None

    for attempt in range(
        1,
        FILE_OPEN_RETRIES + 1,
    ):
        try:
            return load_dataset(
                "parquet",
                data_files={"full": file_url},
                split="full",
                streaming=True,
            )
        except Exception as error:
            last_error = error

            wait_seconds = min(
                2 ** attempt,
                60,
            )

            log(
                f"Dosya açılamadı: {file_path}. "
                f"Deneme {attempt}/{FILE_OPEN_RETRIES}. "
                f"{wait_seconds} saniye beklenecek. "
                f"Hata: {error}"
            )

            write_error(
                category=category,
                file_path=file_path,
                row_number=None,
                error=error,
            )

            time.sleep(wait_seconds)

    raise RuntimeError(
        f"Dosya {FILE_OPEN_RETRIES} denemede açılamadı: "
        f"{file_path}. Son hata: {last_error}"
    )


def iterate_file_documents(
    *,
    category: str,
    file_path: str,
    start_row: int,
) -> Iterator[tuple[int, dict[str, Any]]]:
    file_url = build_file_url(file_path)

    dataset = open_parquet_dataset(
        file_url,
        category,
        file_path,
    )

    for row_number, product in enumerate(dataset):
        if row_number < start_row:
            continue

        try:
            document = transform_product(
                product,
                category,
            )
        except Exception as error:
            write_error(
                category=category,
                file_path=file_path,
                row_number=row_number,
                error=error,
            )
            continue

        if document is None:
            continue

        yield row_number, document


# ============================================================
# BULK INDEXLEME
# ============================================================

def action_from_document(
    document: dict[str, Any],
) -> dict[str, Any]:
    return {
        "_op_type": "index",
        "_index": INDEX_ALIAS,
        "_id": document["parent_asin"],
        "_source": document,
    }


def send_bulk(
    *,
    client: Elasticsearch,
    records: list[tuple[int, dict[str, Any]]],
    category: str,
    file_path: str,
) -> tuple[int, int]:
    if not records:
        return 0, 0

    actions = [
        action_from_document(document)
        for _, document in records
    ]

    success_count, errors = helpers.bulk(
        client,
        actions,
        chunk_size=BULK_CHUNK_SIZE,
        max_chunk_bytes=MAX_CHUNK_BYTES,
        raise_on_error=False,
        raise_on_exception=False,
        max_retries=MAX_RETRIES,
        initial_backoff=INITIAL_BACKOFF_SECONDS,
        max_backoff=MAX_BACKOFF_SECONDS,
        request_timeout=120,
        refresh=False,
    )

    failed_count = len(errors)

    if errors:
        row_by_id = {
            document["parent_asin"]: row_number
            for row_number, document in records
        }

        for error_item in errors:
            operation_name, operation_result = next(
                iter(error_item.items())
            )

            document_id = operation_result.get("_id")
            error_detail = operation_result.get("error")
            status = operation_result.get("status")

            write_error(
                category=category,
                file_path=file_path,
                row_number=row_by_id.get(document_id),
                document_id=document_id,
                error={
                    "operation": operation_name,
                    "status": status,
                    "detail": error_detail,
                },
            )

    return success_count, failed_count


# ============================================================
# DOSYA İŞLEME
# ============================================================

def process_metadata_file(
    *,
    client: Elasticsearch,
    checkpoint: dict[str, Any],
    category: str,
    file_path: str,
) -> tuple[int, int]:
    completed_files = set(
        checkpoint.get("completed_files", [])
    )

    if file_path in completed_files:
        log(f"Dosya daha önce tamamlanmış, atlanıyor: {file_path}")
        return 0, 0

    start_row = 0

    if checkpoint.get("current_file") == file_path:
        start_row = int(
            checkpoint.get("next_row", 0)
        )

    checkpoint["current_file"] = file_path
    checkpoint["next_row"] = start_row
    save_checkpoint(checkpoint)

    log(
        f"Dosya işleniyor: {file_path} | "
        f"başlangıç satırı: {start_row}"
    )

    batch: list[tuple[int, dict[str, Any]]] = []

    file_success = 0
    file_failed = 0
    last_seen_row = start_row - 1

    for row_number, document in iterate_file_documents(
        category=category,
        file_path=file_path,
        start_row=start_row,
    ):
        last_seen_row = row_number
        batch.append((row_number, document))

        if len(batch) < BULK_CHUNK_SIZE:
            continue

        success_count, failed_count = send_bulk(
            client=client,
            records=batch,
            category=category,
            file_path=file_path,
        )

        file_success += success_count
        file_failed += failed_count

        checkpoint["next_row"] = row_number + 1
        checkpoint["total_success"] += success_count
        checkpoint["total_failed"] += failed_count
        save_checkpoint(checkpoint)

        batch.clear()

        processed_in_file = row_number + 1

        if processed_in_file % PROGRESS_INTERVAL < BULK_CHUNK_SIZE:
            log(
                f"{category} | {file_path} | "
                f"okunan satır: {processed_in_file:,} | "
                f"başarılı: {file_success:,} | "
                f"hatalı: {file_failed:,}"
            )

    if batch:
        success_count, failed_count = send_bulk(
            client=client,
            records=batch,
            category=category,
            file_path=file_path,
        )

        file_success += success_count
        file_failed += failed_count

        final_next_row = (
            last_seen_row + 1
            if last_seen_row >= 0
            else start_row
        )

        checkpoint["next_row"] = final_next_row
        checkpoint["total_success"] += success_count
        checkpoint["total_failed"] += failed_count
        save_checkpoint(checkpoint)

        batch.clear()

    completed_files = set(
        checkpoint.get("completed_files", [])
    )
    completed_files.add(file_path)

    checkpoint["completed_files"] = sorted(
        completed_files
    )
    checkpoint["current_file"] = None
    checkpoint["next_row"] = 0
    save_checkpoint(checkpoint)

    log(
        f"Dosya tamamlandı: {file_path} | "
        f"başarılı: {file_success:,} | "
        f"hatalı: {file_failed:,}"
    )

    return file_success, file_failed


# ============================================================
# ELASTICSEARCH
# ============================================================

def create_elasticsearch_client() -> Elasticsearch:
    cloud_id = os.getenv("ELASTIC_CLOUD_ID")
    api_key = os.getenv("ELASTIC_API_KEY")

    if not cloud_id:
        raise RuntimeError(
            "ELASTIC_CLOUD_ID ortam değişkeni tanımlı değil."
        )

    if not api_key:
        raise RuntimeError(
            "ELASTIC_API_KEY ortam değişkeni tanımlı değil."
        )

    return Elasticsearch(
        cloud_id=cloud_id,
        api_key=api_key,
        request_timeout=120,
        max_retries=MAX_RETRIES,
        retry_on_status=(
            429,
            502,
            503,
            504,
        ),
        retry_on_timeout=True,
        http_compress=True,
    )


def validate_elasticsearch(
    client: Elasticsearch,
) -> None:
    log("Elastic Cloud bağlantısı kontrol ediliyor...")

    info = client.info()

    log(
        "Elasticsearch bağlantısı başarılı. "
        f"Cluster: {info.get('cluster_name')} | "
        f"Sürüm: {info.get('version', {}).get('number')}"
    )

    alias_exists = client.indices.exists_alias(
        name=INDEX_ALIAS
    )

    if not alias_exists:
        raise RuntimeError(
            f"{INDEX_ALIAS} alias'ı bulunamadı."
        )

    alias_info = client.indices.get_alias(
        name=INDEX_ALIAS
    )

    write_indices = [
        index_name
        for index_name, index_data in alias_info.items()
        if index_data
        .get("aliases", {})
        .get(INDEX_ALIAS, {})
        .get("is_write_index")
        is True
    ]

    if len(write_indices) != 1:
        raise RuntimeError(
            f"{INDEX_ALIAS} için tam olarak bir write index "
            f"bekleniyordu. Bulunan: {write_indices}"
        )

    log(
        f"Write alias hazır: {INDEX_ALIAS} "
        f"→ {write_indices[0]}"
    )


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    log("=" * 70)
    log("Amazon Reviews 2023 tam metadata import işlemi başlıyor.")
    log(f"Hedef alias: {INDEX_ALIAS}")
    log(f"Dataset revision: {DATASET_REVISION}")
    log("=" * 70)

    try:
        client = create_elasticsearch_client()
        validate_elasticsearch(client)

        checkpoint = load_checkpoint()

        metadata_files = discover_all_metadata_files()

        completed_files = set(
            checkpoint.get("completed_files", [])
        )

        remaining_files = [
            item
            for item in metadata_files
            if item[1] not in completed_files
        ]

        log(
            f"Tamamlanmış dosya: {len(completed_files)} | "
            f"Kalan dosya: {len(remaining_files)}"
        )

        run_success = 0
        run_failed = 0

        for file_number, (
            category,
            file_path,
        ) in enumerate(
            remaining_files,
            start=1,
        ):
            log(
                f"Dosya {file_number}/{len(remaining_files)} "
                f"başlıyor | kategori: {category}"
            )

            try:
                success_count, failed_count = (
                    process_metadata_file(
                        client=client,
                        checkpoint=checkpoint,
                        category=category,
                        file_path=file_path,
                    )
                )

                run_success += success_count
                run_failed += failed_count

            except KeyboardInterrupt:
                log(
                    "İşlem kullanıcı tarafından durduruldu. "
                    "Checkpoint kaydedildi."
                )
                save_checkpoint(checkpoint)
                raise

            except Exception as error:
                checkpoint["total_failed"] += 1
                save_checkpoint(checkpoint)

                write_error(
                    category=category,
                    file_path=file_path,
                    row_number=checkpoint.get("next_row"),
                    error=error,
                )

                log(
                    f"Dosya işlenirken hata oluştu: {file_path} | "
                    f"Hata: {error}"
                )

                log(
                    "Bu dosya tamamlandı olarak işaretlenmedi. "
                    "Script yeniden çalıştırıldığında buradan devam eder."
                )

                # Geçici servis probleminde cluster'ı zorlamamak için bekle.
                time.sleep(30)
                continue

        client.indices.refresh(
            index=INDEX_ALIAS
        )

        count_response = client.count(
            index=INDEX_ALIAS
        )

        log("=" * 70)
        log("Import çalışması tamamlandı.")
        log(f"Bu çalışmada başarılı: {run_success:,}")
        log(f"Bu çalışmada hatalı: {run_failed:,}")
        log(
            "Checkpoint toplam başarılı: "
            f"{checkpoint['total_success']:,}"
        )
        log(
            "Checkpoint toplam hatalı: "
            f"{checkpoint['total_failed']:,}"
        )
        log(
            "Alias üzerindeki toplam belge: "
            f"{count_response['count']:,}"
        )
        log("=" * 70)

    except KeyboardInterrupt:
        print(
            "\nİşlem güvenli şekilde durduruldu. "
            "Aynı komutla devam edebilirsin."
        )
        sys.exit(130)

    except Exception as error:
        log(f"KRİTİK HATA: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()