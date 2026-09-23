"""The second pass that says which candidates belong in the scene at all.

The list this pass edits is the one the operator picks from, so the tests here
are mostly about what it is *not* allowed to do: it may not move a candidate
above one it already outranked, and it may not remove anything it is not both
certain and asked about. A SKU that never appears is a SKU nobody can pick.

Two words, not three. "same" only ever meant "more related than related", and
the operator never acted on the difference — so the question is now the one they
do act on, asked once per scene about the scene's deduplicated SKUs rather than
once per role about a list that overlaps its neighbours.

Which model answers is a setting, so the pass takes an ``ask`` callable and the
tests here hand it one. The clients behind that seam are checked separately: the
same answer has to survive whichever one spoke.
"""

import http.client
import json

import pytest

from store_scenario_inspiration.app import rerank_providers
from store_scenario_inspiration.app.rerank import apply_verdicts, rerank_store, strip_verdicts


def candidate(sku: str, rank: int, *, name: str = "高清摄像头", stock: bool | None = None) -> dict:
    return {
        "rank": rank, "main_sku": sku, "standard_name_cn": name,
        "standard_name_en": "Camera", "rrf_score": 1.0 / rank,
        "channels": ["cn_keyword"], "matched_queries": ["监控摄像头"],
        "country_available": stock, "country_available_quantity": None,
    }


def role(scene_name: str, product_cn: str, skus: list[str], *, stock: bool | None = None) -> dict:
    return {
        "scene_name": scene_name, "product_cn": product_cn, "product_en": f"{product_cn} EN",
        "queries": {"cn": [], "en": []},
        "candidates": [candidate(sku, index, stock=stock) for index, sku in enumerate(skus, 1)],
    }


def verdict(name: str, probability: float | None) -> dict:
    return {"verdict": name, "probability": probability}


def scene_of(*roles: dict) -> dict:
    return {"scenes": list(roles)}


def answering(body) -> object:
    """An ``ask`` that always returns the same verdicts, as a model would."""
    def ask(scene, roles):
        return body([row for item in roles for row in item["candidates"]]), {"total_tokens": 10}
    return ask


# ---------- the rules, which the model cannot reach ----------


def test_only_a_confident_unrelated_verdict_removes_a_candidate() -> None:
    kept, dropped = apply_verdicts(
        [candidate("A", 1), candidate("B", 2), candidate("C", 3), candidate("D", 4)],
        {"A": verdict("unrelated", 0.9), "B": verdict("unrelated", 0.6),
         "C": verdict("related", 1.0), "D": verdict("unrelated", None)},
        cutoff=0.7, drop=True,
    )

    assert [row["main_sku"] for row in dropped] == ["A"]
    # C is the one the scene needs, so it moves above the two uncertain ones.
    assert [row["main_sku"] for row in kept] == ["C", "B", "D"]


def test_marking_only_never_removes_anything() -> None:
    kept, dropped = apply_verdicts(
        [candidate("A", 1), candidate("B", 2)],
        {"A": verdict("unrelated", 0.99), "B": verdict("related", 0.99)},
        cutoff=0.7, drop=False,
    )

    assert dropped == []
    assert sorted(row["main_sku"] for row in kept) == ["A", "B"]


def test_related_candidates_move_up_without_reordering_inside_a_group() -> None:
    """The operator selects "this row and everything above it", so the sort may
    only promote a group — never reshuffle the similarity order a group sits in."""
    kept, _ = apply_verdicts(
        [candidate("A", 1), candidate("B", 2), candidate("C", 3), candidate("D", 4)],
        {"A": verdict("related", 0.9), "B": verdict("related", 0.9),
         "C": verdict("unrelated", 0.1), "D": verdict("related", 0.8)},
        cutoff=0.7, drop=True,
    )

    assert [row["main_sku"] for row in kept] == ["A", "B", "D", "C"]
    assert [row["rank"] for row in kept] == [1, 2, 3, 4]


def test_stock_sorts_inside_a_verdict_and_never_across_one() -> None:
    """Two keys rather than one, because they answer different questions: an
    unrelated row that happens to be in stock must not climb above one the scene
    actually needs. "We did not check" is not "we do not have it", so it does not
    sink to the bottom either."""
    kept, _ = apply_verdicts(
        [candidate("A", 1, stock=True), candidate("B", 2, stock=False),
         candidate("C", 3, stock=None), candidate("D", 4, stock=True)],
        {"A": verdict("unrelated", 0.5), "B": verdict("related", 0.9),
         "C": verdict("related", 0.9), "D": verdict("related", 0.9)},
        cutoff=0.7, drop=True,
    )

    assert [row["main_sku"] for row in kept] == ["D", "C", "B", "A"]


def test_a_candidate_the_model_was_not_asked_about_keeps_its_place() -> None:
    """Silence is not a no: it sorts between the two answers rather than at the
    bottom, and it is never the thing that gets removed."""
    kept, dropped = apply_verdicts(
        [candidate("A", 1), candidate("B", 2), candidate("C", 3)],
        {"A": verdict("unrelated", 0.95), "C": verdict("related", 0.9)},
        cutoff=0.7, drop=True,
    )

    assert [row["main_sku"] for row in kept] == ["C", "B"]
    assert kept[1]["rerank"] is None
    assert [row["main_sku"] for row in dropped] == ["A"]


def test_a_dropped_candidate_is_still_recorded_in_the_scene() -> None:
    """The claim "nothing relevant was thrown away" has to be checkable."""
    kept, dropped = apply_verdicts(
        [candidate("A", 1, name="野餐垫"), candidate("B", 2)],
        {"A": verdict("unrelated", 0.99), "B": verdict("related", 0.9)},
        cutoff=0.7, drop=True,
    )

    assert dropped == [{**candidate("A", 1, name="野餐垫"),
                        "recall_rank": 1, "rerank": "unrelated"}]
    assert kept[0]["rank"] == 1


def test_the_recall_rank_survives_the_reordering() -> None:
    """The rerank renumbers the list, but a candidate keeps the place pure
    similarity gave it — that is where it goes back to if the operator pulls it
    out of the dropped pile."""
    kept, _ = apply_verdicts(
        [candidate("A", 1), candidate("B", 2), candidate("C", 3)],
        {"A": verdict("related", 0.9), "B": verdict("related", 0.9),
         "C": verdict("related", 0.9)},
        cutoff=0.7, drop=True,
    )

    assert [row["main_sku"] for row in kept] == ["A", "B", "C"]
    assert [row["recall_rank"] for row in kept] == [1, 2, 3]


def test_turning_the_pass_off_puts_the_list_back_where_recall_left_it() -> None:
    payload = scene_of(role("遮阳伞", "遮阳棚", ["A", "B"]), role("露营椅", "折叠椅", ["C"]))
    rerank_store(
        payload, provider="deepseek", cut=70, mode="drop",
        ask=answering(lambda rows: {
            row["main_sku"]: verdict("unrelated", 0.99) for row in rows}),
    )

    restored = strip_verdicts(payload)

    assert restored == 3
    assert [row["main_sku"] for row in payload["scenes"][0]["candidates"]] == ["A", "B"]
    assert [row["rank"] for row in payload["scenes"][0]["candidates"]] == [1, 2]
    assert "dropped" not in payload["scenes"][0]
    assert "rerank" not in payload["scenes"][0]["candidates"][0]
    assert "rerank" not in payload


def test_turning_the_pass_off_on_a_list_that_was_never_judged_changes_nothing() -> None:
    payload = scene_of(role("遮阳伞", "遮阳棚", ["A", "B"]))

    assert strip_verdicts(payload) == 0
    assert [row["main_sku"] for row in payload["scenes"][0]["candidates"]] == ["A", "B"]


# ---------- the pass itself ----------


def test_a_scene_is_one_request_not_one_per_role() -> None:
    """Two roles in one scene recall the same SKU, and it is asked about once.
    The answer then has to reach both of them, which is the point of judging the
    scene: it is one answer, shown wherever it applies."""
    seen = []

    def ask(scene, roles):
        seen.append(scene["scene_name"])
        return ({row["main_sku"]: verdict("related", 0.9)
                 for item in roles for row in item["candidates"]}, {})

    payload = scene_of(
        role("遮阳伞", "遮阳棚", ["A"]),
        role("遮阳伞", "遮阳网", ["A", "B"]),
        role("露营椅", "折叠椅", ["C"]),
    )

    result = rerank_store(payload, provider="jev", cut=50, mode="drop", ask=ask)

    assert seen == ["遮阳伞", "露营椅"]
    # A was recalled twice and asked once, so three questions for four list rows.
    assert result["rerank"]["asked"] == 3
    assert result["rerank"]["answered"] == 3
    assert result["rerank"]["dropped"] == 0
    assert result["rerank"]["provider"] == "jev"
    assert result["scenes"][0]["candidates"][0]["rerank"] == "related"
    second = result["scenes"][1]
    assert [row["main_sku"] for row in second["candidates"]] == ["A", "B"]
    assert all(row["rerank"] == "related" for row in second["candidates"])


def test_a_sku_forty_roles_recalled_is_one_question() -> None:
    """The verdict is about the scene, so re-asking under each role buys the
    same answer at forty times the price."""
    roles = [role("S", f"商品{i}", ["A", "B"]) for i in range(40)]

    assert [row["main_sku"] for row in rerank_providers.scene_products(roles)] == ["A", "B"]


def test_a_scene_the_model_refused_does_not_cost_the_others_their_verdicts() -> None:
    def ask(scene, roles):
        if scene["scene_name"] == "遮阳伞":
            raise RuntimeError("TypeSafe 403")
        return ({row["main_sku"]: verdict("related", 1.0)
                 for item in roles for row in item["candidates"]}, {})

    payload = scene_of(role("遮阳伞", "遮阳棚", ["A"]), role("露营椅", "折叠椅", ["B"]))

    result = rerank_store(payload, provider="jev", cut=70, mode="drop", ask=ask)

    assert result["rerank"]["failed"] == 1
    assert result["rerank"]["answered"] == 1
    # The refusal is recorded in the model's own words, so the operator has
    # something to act on rather than just "one scene was not asked".
    assert result["rerank"]["notes"] == ["遮阳伞：TypeSafe 403"]
    # The refused scene keeps every candidate it had, untouched and in place.
    refused = result["scenes"][0]
    assert [row["main_sku"] for row in refused["candidates"]] == ["A"]
    assert "rerank" not in refused["candidates"][0]
    assert result["scenes"][1]["candidates"][0]["rerank"] == "related"


def test_a_scene_with_no_candidates_is_never_asked_about() -> None:
    """A scene that recall came back empty for is not a question — asking it
    would be a request paid for to be told nothing."""
    seen = []

    def ask(scene, roles):
        seen.append(scene["scene_name"])
        return {}, {}

    result = rerank_store(
        scene_of(role("遮阳伞", "遮阳棚", []), role("露营椅", "折叠椅", ["B"])),
        provider="jev", cut=70, mode="drop", ask=ask,
    )

    assert seen == ["露营椅"]
    assert result["rerank"]["asked"] == 1
    assert result["rerank"]["failed"] == 0


def test_the_spend_of_every_scene_lands_in_one_total() -> None:
    payload = scene_of(role("遮阳伞", "遮阳棚", ["A"]), role("露营椅", "折叠椅", ["B"]))

    result = rerank_store(
        payload, provider="deepseek", cut=70, mode="drop",
        ask=answering(lambda rows: {row["main_sku"]: verdict("related", 0.9) for row in rows}),
    )

    assert result["rerank"]["usage"] == {"total_tokens": 20}


@pytest.mark.parametrize("cut,expected", [(60, True), (70, False)])
def test_the_cutoff_is_what_decides_a_borderline_removal(cut, expected) -> None:
    payload = scene_of(role("遮阳伞", "遮阳棚", ["A"]))

    result = rerank_store(
        payload, provider="deepseek", cut=cut, mode="drop",
        ask=answering(lambda rows: {"A": verdict("unrelated", 0.65)}),
    )

    assert (result["rerank"]["dropped"] == 1) is expected


# ---------- the two models behind the seam ----------


@pytest.fixture
def typesafe(monkeypatch):
    """Answer TypeSafe with a fixed body, so the request and the reading of the
    answer can be checked without the network."""
    def install(body: dict) -> dict:
        seen: dict = {}

        def fake_post(url, payload, api_key, *, timeout, label):
            seen.update({"url": url, "payload": payload, "key": api_key, "label": label})
            return body

        monkeypatch.setattr(rerank_providers, "_post", fake_post)
        return seen

    return install


def test_the_typesafe_request_asks_once_per_scene_about_its_own_skus(typesafe) -> None:
    seen = typesafe({"answers": {}})
    roles = [role("家庭安防监控安装", "CCTV监控摄像头", ["A"]),
             role("家庭安防监控安装", "监控电源", ["A", "B"])]

    rerank_providers.ask_typesafe({"scene_name": "家庭安防监控安装"}, roles, api_key="k")

    assert seen["key"] == "k"
    assert seen["payload"]["model"] == "jev-latest"
    # The scene, said once, so no question has to say it again.
    state = seen["payload"]["state"]
    assert state["场景名称"] == "家庭安防监控安装"
    assert state["商品参考示例"] == ["CCTV监控摄像头 EN", "监控电源 EN"]
    assert state["参考示例说明"] == rerank_providers.EXAMPLE_NOTE
    assert state["用途判断规则"] == rerank_providers.JUDGEMENT_RULES
    # A scene written before the scope lists existed is judged without them
    # rather than not judged: the rules and the examples still say what counts.
    assert "本次活动范围" not in state
    # A, recalled by both roles, is one question.
    assert set(seen["payload"]["questions"]) == {"A", "B"}
    decision = seen["payload"]["questions"]["A"]
    assert decision["type"] == "choice"
    # Two options and nothing more: their names are the definition. There is no
    # third one because the operator never acted on one.
    assert set(decision["criteria"]) == {"related", "unrelated"}
    assert all(value is None for value in decision["criteria"].values())
    # Both names, and nothing around them. Measured: a sentence costs 12 tokens a
    # question, and the key is not read by the model at all. The Chinese rides
    # along because English alone is sometimes too generic to judge — it moved
    # 77 verdicts on a 460-SKU scene, in both directions.
    assert decision["instructions"] == "Camera（高清摄像头）"


def test_the_product_names_in_the_state_are_not_repeated_within_a_scene(typesafe) -> None:
    seen = typesafe({"answers": {}})
    roles = [role("S", "遮阳棚", ["A"]), role("S", "遮阳棚", ["B"])]

    rerank_providers.ask_typesafe({"scene_name": "S"}, roles, api_key="k")

    assert seen["payload"]["state"]["商品参考示例"] == ["遮阳棚 EN"]


def test_the_scene_carries_its_own_scope_into_the_question(typesafe) -> None:
    """The two scope lists are the scene's own, and they are the part of the
    state that decides — measured, taking them out leaves the verdict count where
    it was but halves the confidence column's resolution. They are read from the
    scene the run wrote down, so no store's scenes share one set of wording."""
    seen = typesafe({"answers": {}})
    scene_record = {"scene_name": "婚礼布置", "audience": "新人",
                    "scope_in": ["迎宾展示", "桌面装饰"], "scope_out": ["日常办公"]}

    rerank_providers.ask_typesafe(scene_record, [role("婚礼布置", "迎宾牌", ["A"])], api_key="k")

    state = seen["payload"]["state"]
    assert state["本次活动范围"] == ["迎宾展示", "桌面装饰"]
    assert state["不自动扩展的范围"] == ["日常办公"]
    # What the scene is for is not sent: the scope lists say it, in the form the
    # judgement actually uses.
    assert "audience" not in state


def test_a_long_scene_shows_a_few_examples_spread_across_it(typesafe) -> None:
    """Every role name was measured to say no more than a handful; what matters
    is that they come from across the scene rather than all from its front."""
    seen = typesafe({"answers": {}})
    roles = [role("S", f"商品{i}", ["A"]) for i in range(20)]

    rerank_providers.ask_typesafe({"scene_name": "S"}, roles, api_key="k")

    examples = seen["payload"]["state"]["商品参考示例"]
    assert len(examples) == rerank_providers.EXAMPLE_LIMIT
    assert examples[0] == "商品0 EN"
    assert examples[-1] == "商品16 EN"


def test_a_typesafe_choice_becomes_a_verdict_with_its_certainty(typesafe) -> None:
    """The certainty kept is Jev's own ``confidence``, not the chosen option's
    share of ``probabilities``. The two are made to disagree here on purpose, so
    reading the wrong one turns this red."""
    typesafe({"answers": {
        "A": {"type": "choice", "choice": "unrelated", "confidence": 0.55,
              "probabilities": {"related": 0.1, "unrelated": 0.85}},
        "B": {"type": "choice", "choice": "related", "confidence": 0.93,
              "probabilities": {"related": 0.9, "unrelated": 0.0}},
    }})

    verdicts, _ = rerank_providers.ask_typesafe(
        {"scene_name": "S"}, [role("S", "遮阳棚", ["A", "B"])], api_key="k")

    assert verdicts["A"] == {"verdict": "unrelated", "probability": 0.55}
    assert verdicts["B"] == {"verdict": "related", "probability": 0.93}


def test_the_cutoff_reads_a_certainty_the_model_can_actually_be_unsure_about(typesafe) -> None:
    """A two-option choice always gives the option it picked at least half the
    mass, so the chosen option's own probability cannot go below the cutoff's
    lowest setting of 50 — and measured, none of 10,360 real unrelated answers
    did. Driving the cutoff from that number would mean it never holds anything
    back, however far the operator turns it. ``confidence`` is what carries
    "not sure" as far as the decision."""
    typesafe({"answers": {
        # Sure of itself by its own probabilities (0.85), unsure about it (0.45).
        "A": {"type": "choice", "choice": "unrelated", "confidence": 0.45,
              "probabilities": {"related": 0.15, "unrelated": 0.85}},
        "B": {"type": "choice", "choice": "unrelated", "confidence": 0.9,
              "probabilities": {"related": 0.1, "unrelated": 0.9}},
    }})
    verdicts, _ = rerank_providers.ask_typesafe(
        {"scene_name": "S"}, [role("S", "遮阳棚", ["A", "B"])], api_key="k")

    kept, dropped = apply_verdicts(
        [candidate("A", 1), candidate("B", 2)], verdicts, cutoff=0.5, drop=True)

    assert [row["main_sku"] for row in dropped] == ["B"]
    assert [row["main_sku"] for row in kept] == ["A"]


def test_a_typesafe_answer_that_is_missing_or_unreadable_leaves_the_candidate_alone(
    typesafe,
) -> None:
    """A malformed answer is not evidence of anything, least of all of irrelevance."""
    typesafe({"answers": {"A": {"type": "noul", "noul": 0.9},
                          "B": {"type": "choice", "choice": "banana",
                                "probabilities": {"banana": 1.0}}}})

    verdicts, _ = rerank_providers.ask_typesafe(
        {"scene_name": "S"}, [role("S", "遮阳棚", ["A", "B", "C"])], api_key="k")

    assert verdicts == {sku: {"verdict": None, "probability": None}
                        for sku in ("A", "B", "C")}


def test_the_jev_call_is_cached_by_the_exact_bytes_it_asked(tmp_path, monkeypatch) -> None:
    """Re-deciding what to do with the verdicts — moving the cutoff, switching
    between dropping and marking — must not pay for the same question twice, and
    asking something different must not inherit the old answer."""
    asks = []

    def fake_post(url, payload, api_key, *, timeout, label):
        asks.append(payload)
        return {"answers": {"A": {"type": "choice", "choice": "unrelated",
                                  "confidence": 0.9,
                                  "probabilities": {"unrelated": 0.9}}},
                "usage": {"total_tokens": 100}}

    monkeypatch.setattr(rerank_providers, "_post", fake_post)
    roles = [role("S", "遮阳棚", ["A"])]

    first, usage = rerank_providers.ask_typesafe({"scene_name": "S"}, roles, api_key="k", cache_dir=tmp_path)
    second, cached_usage = rerank_providers.ask_typesafe(
        {"scene_name": "S"}, roles, api_key="k", cache_dir=tmp_path)
    rerank_providers.ask_typesafe(
        {"scene_name": "S"}, [role("S", "遮阳棚", ["A", "B"])], api_key="k", cache_dir=tmp_path)

    assert len(asks) == 2
    assert first == second
    assert first["A"]["verdict"] == "unrelated"
    assert usage == {"total_tokens": 100}
    # A re-run that answered from the cache spent nothing, so it reports nothing.
    assert cached_usage == {}
    assert len(list(tmp_path.glob("scene-*.json"))) == 2
    # What is kept is what Jev answered, so a change to how it is read does not
    # need the question to be paid for again.
    assert all(json.loads(path.read_text(encoding="utf-8"))["answers"]["A"]["choice"]
               == "unrelated" for path in tmp_path.glob("scene-*.json"))


def test_a_dropped_connection_costs_one_scene_not_the_whole_run(monkeypatch) -> None:
    """A socket the far end closes mid-answer arrives as a RemoteDisconnected,
    which is not a URLError. Letting it through as itself would escape the
    per-scene guard and take the entire step down over one lost connection."""
    def drop(*args, **kwargs):
        raise http.client.RemoteDisconnected("Remote end closed connection")

    monkeypatch.setattr(rerank_providers.urllib.request, "urlopen", drop)

    with pytest.raises(RuntimeError, match="TypeSafe 无法访问"):
        rerank_providers.ask_typesafe({"scene_name": "S"}, [role("S", "遮阳棚", ["A"])], api_key="k")


def test_a_deepseek_answer_names_the_unrelated_ones_and_silence_is_related() -> None:
    """The model stays quiet about the rest, and that silence is the answer:
    reading it as "unanswered" would report most of a store as never judged."""
    verdicts = rerank_providers.read_deepseek_verdicts(
        json.dumps({"unrelated": [{"main_sku": "A", "confidence": 0.9}]}),
        ["A", "B", "C"],
    )

    assert verdicts["A"] == {"verdict": "unrelated", "probability": 0.9}
    assert verdicts["B"] == {"verdict": "related", "probability": None}
    assert verdicts["C"] == {"verdict": "related", "probability": None}


def test_an_unrelated_row_without_a_confidence_can_never_delete_anything() -> None:
    """A missing number reads as not confident, and not confident is not a
    reason to remove a row."""
    verdicts = rerank_providers.read_deepseek_verdicts(
        json.dumps({"unrelated": [{"main_sku": "A"}]}), ["A"])

    assert verdicts["A"] == {"verdict": "unrelated", "probability": None}


def test_a_sku_the_model_made_up_costs_its_whole_answer_its_credibility() -> None:
    """Invented SKUs are the one way a bad answer could delete a row that was
    never on the list. The row survives because only asked-about SKUs are read,
    and the answer is refused outright because a model naming SKUs it was not
    given has stopped following the instructions that the rest of it relies on —
    a refused answer keeps every candidate, which is the safe way to be wrong."""
    with pytest.raises(RuntimeError, match="未请求的 SKU"):
        rerank_providers.read_deepseek_verdicts(
            json.dumps({"unrelated": [{"main_sku": "NOPE", "confidence": 0.99}]}),
            ["A"],
        )


def test_an_answer_that_is_not_json_is_reported_in_the_operators_words() -> None:
    with pytest.raises(RuntimeError, match="不是 JSON"):
        rerank_providers.read_deepseek_verdicts("好的，我来分析一下", ["A"])


def test_the_deepseek_call_is_cached_by_the_exact_bytes_it_asked(tmp_path, monkeypatch) -> None:
    """Re-deciding which verdicts to act on must not cost a second round of
    calls, and asking something different must not reuse the old answer."""
    asks = []

    def fake_post(url, payload, api_key, *, timeout, label):
        asks.append(payload["messages"][1]["content"])
        return {"choices": [{"message": {"content": json.dumps({"unrelated": []})}}],
                "usage": {"total_tokens": 12}}

    monkeypatch.setattr(rerank_providers, "_post", fake_post)
    roles = [role("S", "遮阳棚", ["A"])]

    first, usage = rerank_providers.ask_deepseek({"scene_name": "S"}, roles, api_key="k", cache_dir=tmp_path)
    second, cached_usage = rerank_providers.ask_deepseek(
        {"scene_name": "S"}, roles, api_key="k", cache_dir=tmp_path)

    assert len(asks) == 1
    assert first == second
    assert first["A"]["verdict"] == "related"
    assert usage == {"total_tokens": 12}
    # A re-run that answered from the cache spent nothing, so it reports nothing.
    assert cached_usage == {}
    assert len(list(tmp_path.glob("scene-*.json"))) == 1
    # What is kept is the model's own words, so a change to how they are read
    # does not need the call to be paid for again.
    assert json.loads(next(tmp_path.glob("scene-*.json")).read_text(encoding="utf-8"))[
        "content"] == json.dumps({"unrelated": []})


def test_a_scene_too_big_for_one_reply_is_asked_in_slices(tmp_path, monkeypatch) -> None:
    """DeepSeek has to write every unrelated SKU out in full, so its ceiling is
    the size of the answer. The slice a SKU landed in is part of its question,
    which is what keeps the cache key honest about it."""
    monkeypatch.setattr(rerank_providers, "DEEPSEEK_SLICE", 2)
    asks = []

    def fake_post(url, payload, api_key, *, timeout, label):
        asks.append(payload)
        return {"choices": [{"message": {"content": json.dumps({"unrelated": []})}}],
                "usage": {"total_tokens": 10}}

    monkeypatch.setattr(rerank_providers, "_post", fake_post)

    verdicts, usage = rerank_providers.ask_deepseek(
        {"scene_name": "S"}, [role("S", "遮阳棚", ["A", "B", "C"])], api_key="k", cache_dir=tmp_path)

    assert len(asks) == 2
    assert sorted(verdicts) == ["A", "B", "C"]
    # Two replies, one total: the caller sees one scene's spend, not two.
    assert usage == {"total_tokens": 20}
    assert len(list(tmp_path.glob("scene-*.json"))) == 2
