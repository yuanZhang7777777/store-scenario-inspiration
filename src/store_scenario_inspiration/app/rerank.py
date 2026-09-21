"""Decide which candidates survive the second look, and record what left.

Fused similarity is good at getting the right SKU somewhere into the thirty and
bad at saying which of the thirty it is: the top of the list is full of
accessories, near-synonyms and unrelated things that happen to share words.
Asking a model is what fixes that. *Which* model answers is a setting — Jev by
default, DeepSeek as the spare — so the clients live in ``rerank_providers`` and
this module only ever sees a verdict. Switching models changes nothing about
what happens next.

The question is asked per scene, not per product role: the roles are how the
scene is displayed, not how it is judged. Recall answers thirty candidate lists
that overlap heavily, and asking each list separately judged the same SKU once
per role that happened to recall it.

Recall is the side that must not break. A SKU the operator never sees is a SKU
they cannot pick, and the catalogue is 8,000 items nobody can hold in their
head. So this pass is deliberately lopsided:

* only a candidate the model calls unrelated *and* is confident about leaves the
  list — anything it is unsure of stays and is marked;
* dropping is off by a word from the operator if it ever eats something real;
* every removed SKU is written into the artifact, so "nothing relevant was lost"
  is a claim that can be checked rather than trusted.
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .params import RERANK_MARK_ONLY
from .rerank_providers import RELATED, UNRELATED, Verdicts, scene_products


SCHEMA_RERANK = "store-rerank-v1"

# Where a verdict sorts, then where its stock sorts, then nothing — the order
# recall already had. Two keys rather than one, because they answer different
# questions: an unrelated row that happens to be in stock must not climb above
# one the scene actually needs.
#
# An unanswered candidate sits between the two: the model saying nothing is not
# the model saying no. Stock has the same shape — "we did not check" is not
# "we do not have it", so it does not sink to the bottom either.
GROUP = {RELATED: 0, None: 1, UNRELATED: 2}
STOCK = {True: 0, None: 1, False: 2}

WORKERS = 4

# One scene's worth of questions, and the model's spend answering them.
# Every provider answers in this shape, so the switch cannot reach the rules.
Ask = Callable[[str, list[dict]], tuple[Verdicts, dict]]


def _is_confidently_unrelated(verdict: dict | None, cutoff: float) -> bool:
    """The only way a candidate leaves the list, and it has to earn it.

    A missing probability counts as not confident: a model returning a verdict
    without one is not a reason to delete a row.
    """
    if not verdict or verdict["verdict"] != UNRELATED:
        return False
    probability = verdict["probability"]
    return probability is not None and probability >= cutoff


def apply_verdicts(
    candidates: list[dict], verdicts: dict[str, dict], *, cutoff: float, drop: bool
) -> tuple[list[dict], list[dict]]:
    """Split one product role's candidates, then put what belongs in the scene first.

    The sort is stable, so a candidate still sits above exactly the candidates it
    already outranked inside its group — "select this row and everything above it"
    keeps meaning what the operator thinks it means.

    ``recall_rank`` remembers where pure similarity had the candidate before this
    pass reordered anything. It is the handle the operator pulls when they fish a
    dropped candidate back: the restored row lands where the recall put it, not
    wherever the list happened to end.
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    for candidate in candidates:
        verdict = verdicts.get(candidate["main_sku"])
        named = (verdict or {}).get("verdict")
        row = {**candidate, "recall_rank": candidate["rank"], "rerank": named}
        if drop and _is_confidently_unrelated(verdict, cutoff):
            dropped.append(row)
            continue
        kept.append(row)
    kept.sort(key=lambda row: (GROUP[row["rerank"]],
                               STOCK.get(row.get("country_available"), 1)))
    for rank, candidate in enumerate(kept, start=1):
        candidate["rank"] = rank
    return kept, dropped


def strip_verdicts(payload: dict) -> int:
    """Undo a verdict pass, so "off" means the pure recall list and not a stale one.

    The dropped candidates are all still in the payload — that is what the audit
    list is for — so turning the judgement off is putting them back where pure
    similarity had them and forgetting the labels. Returns how many came back.
    """
    restored = 0
    for scene in payload.get("scenes") or []:
        rows = (scene.get("candidates") or []) + (scene.get("dropped") or [])
        if scene.get("dropped"):
            rows.sort(key=lambda row: row.get("recall_rank") or row.get("rank") or 0)
            restored += len(scene["dropped"])
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
            row.pop("recall_rank", None)
            row.pop("rerank", None)
        scene["candidates"] = rows
        scene.pop("dropped", None)
    payload.pop("rerank", None)
    return restored


def rerank_scenes(
    roles: list[dict], *, ask: Ask, cutoff: float, drop: bool
) -> tuple[list[dict], dict]:
    """One verdict pass per scene, in parallel over the network.

    The roles keep their own candidate lists and are handed back unchanged in
    shape — a SKU judged once for the scene carries that verdict into every role
    that recalled it, which is the whole point: it is one answer, shown wherever
    it applies.
    """
    summary = {"asked": 0, "answered": 0, "dropped": 0, "failed": 0, "notes": [], "usage": {}}
    scenes: dict[str, list[dict]] = OrderedDict()
    for role in roles:
        scenes.setdefault(role["scene_name"], []).append(role)

    def one(item: tuple[str, list[dict]]):
        scene_name, scene_roles = item
        if not any(role.get("candidates") for role in scene_roles):
            return item, None, None, {}
        try:
            verdicts, usage = ask(scene_name, scene_roles)
        except RuntimeError as error:
            return item, None, str(error), {}
        return item, verdicts, None, usage

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        outcomes = list(pool.map(one, scenes.items()))

    for (scene_name, scene_roles), verdicts, error, usage in outcomes:
        summary["asked"] += len(scene_products(scene_roles))
        for key, value in usage.items():
            if isinstance(value, int) and isinstance(summary["usage"].get(key), int):
                summary["usage"][key] += value
            else:
                summary["usage"][key] = value
        if error is not None:
            # One unanswered scene must not cost the other seven their verdicts,
            # and it must never cost it its candidates either.
            summary["failed"] += 1
            if len(summary["notes"]) < 3:
                summary["notes"].append(f"{scene_name}：{error}")
            continue
        if verdicts is None:
            continue
        summary["answered"] += sum(
            1 for verdict in verdicts.values() if verdict["verdict"] is not None
        )
        for role in scene_roles:
            kept, dropped = apply_verdicts(
                role["candidates"], verdicts, cutoff=cutoff, drop=drop
            )
            role["candidates"] = kept
            role["dropped"] = dropped
            summary["dropped"] += len(dropped)
    return roles, summary


def rerank_store(
    payload: dict, *, ask: Ask, cut: int, mode: str, provider: str
) -> dict:
    """Add verdicts to one retrieval payload, in place, and describe what changed."""
    scenes, summary = rerank_scenes(
        payload.get("scenes") or [],
        ask=ask,
        cutoff=cut / 100,
        drop=mode != RERANK_MARK_ONLY,
    )
    summary["mode"] = mode
    summary["cutoff"] = cut
    summary["provider"] = provider
    payload["scenes"] = scenes
    payload["rerank"] = {"schema": SCHEMA_RERANK, **summary}
    return payload
