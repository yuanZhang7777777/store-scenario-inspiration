"""The knobs an operator can turn, in one place.

Each field carries the sentence the UI shows next to it, so the form and the
validation can never drift apart. Params are stored per store: the same shop is
run again and again while the operator tunes it, and a setting that only lived
in a request body would be lost between runs.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from pydantic import BaseModel, Field


SCHEMA_PARAMS = "store-params-v1"

STOCK_ALL = "all"
STOCK_IN_ONLY = "in_stock"
STOCK_FILTERS = (STOCK_ALL, STOCK_IN_ONLY)

RERANK_DROP = "drop"
RERANK_MARK_ONLY = "mark_only"
# Marking first: it is the default, and the form reads the tuple top to bottom.
RERANK_MODES = (RERANK_MARK_ONLY, RERANK_DROP)

RERANK_DEEPSEEK = "deepseek"
RERANK_JEV = "jev"
RERANK_OFF = "off"
# Jev first: it is the one trained to return calibrated probabilities and it
# charges only for input, so it is both the better answer and the cheaper one.
RERANK_PROVIDERS = (RERANK_JEV, RERANK_DEEPSEEK, RERANK_OFF)

DEFAULTS = {
    "scene_count": 6,
    "products_per_scene": 16,
    "expansion_terms": 6,
    "recall_limit": 30,
    "stock_filter": STOCK_ALL,
    "rerank": RERANK_MARK_ONLY,
    "rerank_cutoff": 50,
    "rerank_provider": RERANK_JEV,
    "temperature": 0.2,
}

# The knobs the settings form renders as a dropdown rather than a number.
CHOICES = {
    "stock_filter": (
        STOCK_FILTERS,
        {
            STOCK_ALL: "都留着，标出有没有货",
            STOCK_IN_ONLY: "只看目标国家有货的",
        },
    ),
    "rerank": (
        RERANK_MODES,
        {
            RERANK_MARK_ONLY: "不排除，都留着（只标出来）",
            RERANK_DROP: "排除，不相关的去掉",
        },
    ),
    "rerank_provider": (
        RERANK_PROVIDERS,
        {
            RERANK_JEV: "要，用 Jev 标一遍",
            RERANK_DEEPSEEK: "要，用 DeepSeek 标一遍",
            RERANK_OFF: "不要，直接用搜出来的结果",
        },
    ),
}


class SearchParams(BaseModel):
    """Everything the scene, expansion and retrieval stages read."""

    scene_count: int = Field(
        default=6, ge=3, le=8,
        description=(
            "生成几个场景。每个场景现在单独跑一次，所以调大不再拖累质量，只影响要等多久。"
            "场景之间要能拉开差别：同一个场景在不同天气、人数、场地、时段下要用的商品是不一样的。"
        ),
    )
    products_per_scene: int = Field(
        default=16, ge=8, le=40,
        description=(
            "每个场景至少列出几样商品，含各种条件下额外要用到的。这是这一页最该调大的数："
            "列得全，才看得出这个场景要配齐什么。"
            "注意每多一个商品角色，后面就多一次召回和一次判断，等得更久。"
        ),
    )
    expansion_terms: int = Field(
        default=6, ge=0, le=12,
        description="每个商品在中英文各扩写几个近义词。扩得多召回更全，也更容易拉进隔壁品类。",
    )
    recall_limit: int = Field(
        default=30, ge=5, le=200,
        description="每个商品保留多少个候选 SKU，按相关度从高到低排。",
    )
    stock_filter: str = Field(
        default=STOCK_ALL, pattern="^(all|in_stock)$",
        description=(
            "看全部：搜到的都留着，每条标注目标国家能不能发。"
            "只看有货：把没货的去掉，列表更短，但有些商品整个就不在这个国家备货，"
            "会被一起筛掉、事后看不出来。"
        ),
    )
    rerank: str = Field(
        default=RERANK_MARK_ONLY, pattern="^(drop|mark_only)$",
        description=(
            "召回之后，模型会逐个场景看一遍：这个商品和这个场景相不相关，"
            "标上「相关」或「不相关」。它只是怀疑，不是判决。"
            "不排除（默认）：一条都不删，只打标记。排除：标为不相关的直接从列表里去掉。"
            "排除这一档要小心——实测它会把整个商品剔空：泰国店的「驱蚊液」召回回来的"
            "灭蚊灯、灭螨驱蚊虫仪、捕虫器全被标成不相关去掉，这个商品最后一条候选都不剩。"
            "去掉的那些仍然记在文件里。"
        ),
    )
    rerank_provider: str = Field(
        default=RERANK_JEV, pattern="^(jev|deepseek|off)$",
        description=(
            "召回之后由谁来标一遍相关 / 不相关。"
            "Jev：TypeSafe 的模型，只为输入计费、输出不计费，一个场景一次请求，"
            "跑完一遍的花费基本可以忽略；它输出的是概率，比一句话结论更靠得住。"
            "DeepSeek：按量付费，跑完一遍大约几毛钱。"
            "不要：候选列表就是纯搜出来的结果，一样能挑。随时可以换。"
        ),
    )
    rerank_cutoff: int = Field(
        default=50, ge=50, le=95,
        description=(
            "模型说一条不相关时，它自己有多确定才当真，单位是百分比。"
            "判定只有相关、不相关两档，50 就是模型自己那一档直接认——"
            "它说不相关就信它；调高更保守，要它更笃定才算数。"
            "只对「排除」这一档有实际影响——选「不排除」的时候，标多标少都不动列表。"
        ),
    )
    temperature: float = Field(
        default=0.2, ge=0, le=1,
        description=(
            "官方文档（temperature）：采样温度，取值 0 到 2，默认 1。"
            "数值越高（如 0.8）输出越随机，越低（如 0.2）越集中、越确定；"
            "官方建议不要同时改 temperature 和 top_p。"
            "文档注明「思考模式下不生效」——我们这条链路关掉了思考，所以它在这里生效。"
            "这里只放到 1.0：实测再往上生成不成功过。"
        ),
    )
    @classmethod
    def schema_for_ui(cls) -> dict:
        """The field list the settings form renders, with its explanations."""
        schema = cls.model_json_schema()
        return {
            "fields": [
                {
                    "name": name,
                    "default": field["default"],
                    "minimum": field.get("minimum"),
                    "maximum": field.get("maximum"),
                    # The knobs that take decimals need to say so, or the form
                    # steps in whole numbers and 0.5 is unreachable.
                    "step": 0.1 if field.get("type") == "number" else 1,
                    "options": list(CHOICES[name][0]) if name in CHOICES else None,
                    "labels": CHOICES[name][1] if name in CHOICES else None,
                    "description": field["description"],
                }
                for name, field in schema["properties"].items()
            ]
        }


def _bound(name: str) -> tuple[int | None, int | None]:
    """The low and high a numeric field allows, read off the field itself."""
    metadata = SearchParams.model_fields[name].metadata
    low = next((item.ge for item in metadata if hasattr(item, "ge")), None)
    high = next((item.le for item in metadata if hasattr(item, "le")), None)
    return low, high


def settled(fields: dict) -> dict:
    """Pull a stored setting into the range the form now allows.

    The ranges are still being tuned, and a store tuned under an older one would
    otherwise become unopenable — its page could not even render the form that
    would let the operator fix it. Clamping keeps the store usable; the numbers
    it lands on are visible in the form, so nothing changes behind the operator's
    back without them seeing it.
    """
    for name, field in SearchParams.model_fields.items():
        value = fields.get(name)
        # floats count too: the temperature ceiling was lowered after a store had
        # already been saved at the old one, and skipping it would have failed
        # validation on open — the exact thing this function exists to prevent.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        low, high = _bound(name)
        fields[name] = max(low, min(high, value)) if low is not None and high is not None else value
    return fields


def load_params(path: Path) -> SearchParams:
    if not path.is_file():
        return SearchParams()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != SCHEMA_PARAMS:
        raise ValueError(f"invalid params: {path}")
    return SearchParams(**settled({k: v for k, v in value.items() if k != "schema"}))


def save_params(path: Path, params: SearchParams) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": SCHEMA_PARAMS, **params.model_dump()}
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        temporary = Path(handle.name)
    temporary.replace(path)
