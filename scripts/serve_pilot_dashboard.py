"""Serve the local store-analysis pilot dashboard with Python's standard library."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


DEFAULT_DATA_DIR = Path(r"E:\Project\store-assortment-copilot\var\pilot")
INDEX_FILE = Path(__file__).resolve().parents[1] / "frontend" / "index.html"


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"缺少数据文件: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _project_retrieval(document: dict, *, is_final: bool = False) -> dict:
    fields = (
        "rank",
        "main_sku",
        "standard_name_cn",
        "standard_name_en",
        "rrf_score",
        "country_available_quantity",
        "country_available",
        "relevance",
        "rerank_reason",
    )
    scenes = []
    for scene in document.get("scenes", []):
        products = []
        for product in scene.get("products", []):
            item = {
                key: product.get(key)
                for key in (
                    "product_cn",
                    "product_en",
                    "canonical_cn",
                    "canonical_en",
                    "expanded_cn",
                    "expanded_en",
                )
            }
            groups = (
                "related_candidates",
                "country_recommendations",
                "raw_global_candidates",
            ) if is_final else ("global_candidates", "country_candidates")
            for group in groups:
                item[group] = [
                    {key: candidate.get(key) for key in fields}
                    for candidate in product.get(group, [])
                ]
            products.append(item)
        scenes.append({"scene_name": scene.get("scene_name"), "products": products})
    return {
        "model": document.get("model"),
        "country": document.get("country"),
        "inventory_coverage": document.get("inventory_coverage", "available"),
        "elapsed_total_seconds": document.get("elapsed_total_seconds"),
        "is_final": is_final,
        "recommended_main_skus": [
            {key: candidate.get(key) for key in fields}
            for candidate in document.get("recommended_main_skus", [])
        ] if is_final else [],
        "scenes": scenes,
    }


def build_payload(data_dir: Path) -> dict:
    sample = _read_json(data_dir / "sample_store.json")
    models = {}
    for key, analysis_name, retrieval_name, final_name in (
        ("gpt55", "gpt55_analysis.json", "gpt_55_retrieval.json", "gpt55_final.json"),
        ("deepseek", "deepseek_analysis.json", "deepseek_flash_retrieval.json", "deepseek_final.json"),
    ):
        final_path = data_dir / final_name
        analysis_path = data_dir / analysis_name
        retrieval_path = data_dir / "retrieval" / retrieval_name
        final = _read_json(final_path) if final_path.is_file() else None
        if not final and not analysis_path.is_file():
            continue
        analysis = final.get("analysis") if final else _read_json(analysis_path)
        retrieval = final or (_read_json(retrieval_path) if retrieval_path.is_file() else {
            "model": analysis.get("model"),
            "country": sample.get("store", {}).get("country"),
            "scenes": [],
        })
        models[key] = {
            "analysis": analysis,
            "retrieval": _project_retrieval(retrieval, is_final=bool(final)),
        }
    return {
        "store": sample.get("store", {}),
        "source": sample.get("source", {}),
        "screenshot_source": sample.get("screenshot_source", {}),
        "limitations": sample.get("limitations", []),
        "models": models,
    }


def discover_stores(data_dir: Path) -> tuple[list[dict], str]:
    manifest_path = data_dir / "manifest.json"
    manifest = _read_json(manifest_path) if manifest_path.is_file() else {}
    entries = manifest.get("stores", [])
    store_dirs: list[tuple[str, Path]] = []
    if entries:
        for entry in entries:
            store_id = entry if isinstance(entry, str) else entry.get("id")
            relative_path = f"stores/{store_id}" if isinstance(entry, str) else entry.get("path", f"stores/{store_id}")
            path = (data_dir / relative_path).resolve()
            if store_id and (path / "sample_store.json").is_file():
                store_dirs.append((str(store_id), path))
    elif (data_dir / "sample_store.json").is_file():
        store_dirs.append((data_dir.name, data_dir.resolve()))
    else:
        stores_root = data_dir / "stores"
        store_dirs.extend(
            (path.parent.name, path.parent.resolve())
            for path in sorted(stores_root.glob("*/sample_store.json"))
        )
    if not store_dirs:
        raise FileNotFoundError(f"没有找到店铺结果: {data_dir}")

    stores = []
    for store_id, path in store_dirs:
        store = _read_json(path / "sample_store.json").get("store", {})
        stores.append({
            "id": store_id,
            "label": store.get("real_store_name") or store.get("store_name") or store_id,
            "store_name": store.get("store_name"),
            "country": store.get("country"),
            "owner": store.get("owner"),
            "path": path,
        })
    default_id = manifest.get("default_store_id") or stores[0]["id"]
    if default_id not in {store["id"] for store in stores}:
        default_id = stores[0]["id"]
    return stores, default_id


def make_handler(stores: list[dict], default_store_id: str):
    store_paths = {store["id"]: store["path"] for store in stores}
    store_index = {
        "default_store_id": default_store_id,
        "stores": [{key: value for key, value in store.items() if key != "path"} for store in stores],
    }
    encoded_store_index = json.dumps(store_index, ensure_ascii=False).encode("utf-8")
    encoded_index = INDEX_FILE.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            path = urlparse(self.path).path
            if path == "/api/stores":
                self._send(200, "application/json; charset=utf-8", encoded_store_index)
            elif path == "/api/data":
                self._send_store(default_store_id)
            elif path.startswith("/api/stores/"):
                self._send_store(unquote(path.removeprefix("/api/stores/")))
            elif path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", encoded_index)
            elif path == "/health":
                self._send(200, "application/json", json.dumps({"ok": True, "stores": len(stores)}).encode())
            else:
                self._send(404, "text/plain; charset=utf-8", "未找到".encode("utf-8"))

        def _send_store(self, store_id: str) -> None:
            data_path = store_paths.get(store_id)
            if data_path is None:
                self._send(404, "application/json; charset=utf-8", b'{"error":"unknown store"}')
                return
            payload = build_payload(data_path)
            payload["store_id"] = store_id
            self._send(
                200,
                "application/json; charset=utf-8",
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"{self.client_address[0]} - {fmt % args}")

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    stores, default_store_id = discover_stores(args.data_dir)
    if args.check:
        for store in stores:
            payload = build_payload(store["path"])
            assert payload["store"].get("store_name"), f"店铺名称为空: {store['id']}"
            print(f"OK: {store['id']}，可用模型 {list(payload['models'])}")
        return

    server = ThreadingHTTPServer((args.host, args.port), make_handler(stores, default_store_id))
    print(f"Dashboard: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
