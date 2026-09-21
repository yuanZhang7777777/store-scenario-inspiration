"""Runtime settings, overridable with ``SSI_``-prefixed environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SSI_", extra="ignore")

    data_dir: Path = Path(r"E:\Project\store-assortment-copilot\var\pilot")
    asset_db: Path = Path(r"E:\Project\store-assortment-copilot\var\product-asset\catalog.sqlite3")
    vector_cache: Path = Path(
        r"E:\Project\store-assortment-copilot\var\product-asset\vectors"
        r"\bge-large-v1.5-1024\embeddings.sqlite3"
    )
    model_cache: Path = Path(
        r"E:\Project\store-assortment-copilot\var\models\huggingface-transformers"
    )
    stock: Path = Path(
        r"E:\download\Chrome下载\真仓库存明细数据-普通商品-汇总数据-1789685524432.xlsx"
    )
    qdrant: str = "http://127.0.0.1:6333"
    collection: str = "product_asset_bilingual_1024_v1"
    deepseek_api_key: str = ""
    typesafe_api_key: str = ""
    typesafe_url: str = "https://api.typesafe.ai/v1/systemone"
    countries: tuple[str, ...] = ("PH", "TH", "VN", "MY")
    max_upload_bytes: int = 8 * 1024 * 1024

    @property
    def api_key(self) -> str:
        """The DeepSeek key, accepting the name the existing scripts already use."""
        return self.deepseek_api_key.strip() or os.environ.get("DEEPSEEK_API_KEY", "").strip()

    @property
    def typesafe_key(self) -> str:
        """The TypeSafe key, under either of the names that mean it."""
        return self.typesafe_api_key.strip() or os.environ.get(
            "TYPESAFE_API_KEY", ""
        ).strip()
