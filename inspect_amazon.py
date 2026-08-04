import argparse
import os
from itertools import islice

os.environ["HF_HUB_DISABLE_XET"] = "1"

from datasets import load_dataset

SOURCE_CATEGORY = "Cell_Phones_and_Accessories"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ham Hugging Face Amazon kayıtlarını inceler (Elasticsearch'e bağlanmaz)."
    )
    parser.add_argument(
        "--quality",
        action="store_true",
        help=(
            "Her örnek kayıt için index_amazon.transform_product ile üretilen "
            "belgeyi ve product_quality.evaluate_product_quality sonucunu "
            "(quality_explanation dahil) da yazdır."
        ),
    )
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="İncelenecek örnek kayıt sayısı (varsayılan: 3).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    parquet_url = (
        "https://huggingface.co/datasets/"
        "McAuley-Lab/Amazon-Reviews-2023/"
        "resolve/main/"
        f"raw_meta_{SOURCE_CATEGORY}/"
        "full-00000-of-00007.parquet"
    )

    dataset = load_dataset(
        "parquet",
        data_files={"full": parquet_url},
        split="full",
        streaming=True,
    )

    transform_product = None
    evaluate_product_quality = None
    if args.quality:
        # Kalite algoritması kopyalanmaz — index_amazon.py / product_quality.py
        # ile AYNI fonksiyonlar import edilir.
        from index_amazon import transform_product
        from product_quality import evaluate_product_quality

    for product in islice(dataset, args.count):
        images = product.get("images") or {}
        high_resolution_images = images.get("hi_res") or []
        large_images = images.get("large") or []

        image_url = None

        if high_resolution_images:
            image_url = high_resolution_images[0]
        elif large_images:
            image_url = large_images[0]

        print("\n" + "=" * 70)
        print("parent_asin:", product.get("parent_asin"))
        print("title:", product.get("title"))
        print("main_category:", product.get("main_category"))
        print("categories:", product.get("categories"))
        print("store:", product.get("store"))
        print("price:", product.get("price"))
        print("average_rating:", product.get("average_rating"))
        print("rating_number:", product.get("rating_number"))
        print("features:", product.get("features"))
        print("description:", product.get("description"))
        print("image_url:", image_url)

        if args.quality:
            document = transform_product(product, SOURCE_CATEGORY)
            if document is None:
                print("quality: (transform_product None döndürdü — parent_asin/title eksik)")
                continue
            quality = evaluate_product_quality(document, include_explanation=True)
            print("quality:", quality)


if __name__ == "__main__":
    main()