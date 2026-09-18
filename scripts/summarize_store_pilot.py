"""Print compact top-five recall results from pilot JSON files."""

import json
from pathlib import Path
import sys


for path_text in sys.argv[1:]:
    path = Path(path_text)
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"MODEL {data['model']}")
    for scene in data["scenes"]:
        for product in scene["products"]:
            global_top = " | ".join(
                f"{row['main_sku']}:{row['standard_name_cn']}"
                for row in product["global_candidates"][:5]
            )
            country_top = " | ".join(
                f"{row['main_sku']}:{row['standard_name_cn']}({row['country_available_quantity']})"
                for row in product["country_candidates"][:5]
            )
            print(f"{scene['scene_name']} / {product['product_cn']}")
            print(f"  GLOBAL {global_top or '[none]'}")
            print(f"  COUNTRY {country_top or '[none]'}")
