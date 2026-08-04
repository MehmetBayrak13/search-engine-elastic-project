import os
from itertools import islice

os.environ["HF_HUB_DISABLE_XET"] = "1"

from datasets import load_dataset


def main() -> None:
    parquet_url = (
        "https://huggingface.co/datasets/"
        "McAuley-Lab/Amazon-Reviews-2023/"
        "resolve/main/"
        "raw_meta_Cell_Phones_and_Accessories/"
        "full-00000-of-00007.parquet"
    )

    dataset = load_dataset(
        "parquet",
        data_files={"full": parquet_url},
        split="full",
        streaming=True,
    )

    for product in islice(dataset, 3):
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


if __name__ == "__main__":
    main()