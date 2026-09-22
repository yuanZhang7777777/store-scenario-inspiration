import json

import pytest

from store_scenario_inspiration.pipeline.analysis import (
    MAX_TOKENS,
    MODEL,
    PRODUCT_SLACK,
    analyze_expansions,
    analyze_scene_products,
    analyze_scenes,
    analyze_synthesis,
    assemble,
    normalize_products,
    normalize_scenes,
    validate,
    validate_products,
    validate_scenes,
    validate_synthesis,
)


def section(judgement: str = "判断", evidence: str = "依据", **extra) -> dict:
    return {"judgement": judgement, "evidence": evidence, **extra}


def response(content: str, *, finish_reason: str = "stop", completion_tokens: int = 100) -> dict:
    return {
        "id": "chat-1",
        "model": MODEL,
        "usage": {"completion_tokens": completion_tokens, "total_tokens": 200},
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
    }


def scene(name: str = "周末露营") -> dict:
    return {"scene_name": name, "audience": "一家人", "user_need": "周末带孩子出去", "evidence": "截图"}


def product(name: str = "帐篷") -> dict:
    return {"product_cn": name, "product_en": "Tent", "purpose": "过夜"}


def synthesis() -> dict:
    return {
        "model": MODEL,
        "manager_summary": {
            "executive_conclusion": "结论",
            "business_opportunity": "机会",
            "recommended_actions": ["一", "二", "三"],
            "decision_boundary": "边界",
        },
        "store_profile": section(),
        "audiences": [{"audience_name": "人", "description": "描述", "evidence": "依据"}],
        "current_product_structure": section(),
        "future_product_structure": section(priority_order=["甲", "乙", "丙"]),
        "operation_strategy": [{"strategy_name": "策略", "description": "描述", "evidence": "依据"}],
    }


def serve(monkeypatch, body: dict) -> None:
    class Fake:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(body, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Fake())


def record(monkeypatch, answer: dict | None = None) -> list[dict]:
    """Serve a well-formed answer and keep every payload the module sent.

    By default the answer carries both scenes and products, so it fits either of
    the two calls that produce them; what these tests are about is what went out,
    not what came back.
    """
    sent: list[dict] = []

    class Fake:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            content = json.dumps(
                answer or {"model": MODEL, "scenes": [scene()],
                           "products": [product("帐篷"), product("睡袋")]},
                ensure_ascii=False,
            )
            return json.dumps(response(content), ensure_ascii=False).encode("utf-8")

    original = __import__("urllib.request", fromlist=["Request"]).Request

    def remember(url, data=None, headers=None):
        sent.append(json.loads(data.decode("utf-8")))
        return original(url, data=data, headers=headers)

    monkeypatch.setattr("urllib.request.Request", remember)
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: Fake())
    return sent


# ---------- the scene skeletons ----------

def test_a_scene_count_that_is_off_by_one_is_still_usable() -> None:
    """The count is what we asked for, not a contract. Throwing away a call that
    already cost money over an off-by-one is not a trade worth making."""
    validate_scenes({"model": MODEL, "scenes": [scene(), scene("居家办公")]}, scene_count=3)


def test_a_scene_that_carries_products_anyway_is_rejected() -> None:
    """This call exists to be small. A model that writes the products here as
    well hands back exactly the giant answer the split was made to avoid."""
    value = {"model": MODEL, "scenes": [{**scene(), "product_needs": [product()]}]}

    with pytest.raises(ValueError, match="scene fields mismatch"):
        validate_scenes(value, scene_count=1)


def test_two_scenes_with_the_same_name_are_rejected() -> None:
    """The products come back keyed by scene name, so a duplicate would hand one
    scene's roles to the other."""
    with pytest.raises(ValueError, match="share a name"):
        validate_scenes({"model": MODEL, "scenes": [scene("露营"), scene("露营")]}, scene_count=2)


def test_volunteered_scene_fields_are_dropped_rather_than_stored() -> None:
    normalized = normalize_scenes(
        {"scenes": [{**scene(), "priority": "高", "notes": "随便写的"}]})

    assert normalized == [scene()]


def test_a_hot_scene_answer_is_read_rather_than_thrown_away(monkeypatch) -> None:
    """The model sometimes presses enter inside a sentence instead of writing \\n.
    Strict JSON calls that illegal, but the answer is whole and already paid for."""
    text = json.dumps({"model": MODEL, "scenes": [scene()]}, ensure_ascii=False)
    text = text.replace('"user_need": "周末带孩子出去"', '"user_need": "周末带孩子出去\n第二行"')
    assert '"周末带孩子出去\n第二行"' in text
    serve(monkeypatch, response(text))

    result, _ = analyze_scenes({"store": {}}, "secret-key", scene_count=1)

    assert result["scenes"][0]["user_need"] == "周末带孩子出去\n第二行"


# ---------- one scene's products ----------

def test_a_product_list_that_overshoots_the_ask_is_trimmed_not_thrown_away() -> None:
    """The count is a floor we ask for, so a model that lists more than asked is
    answering well. Each extra row costs a retrieval and a verdict though, so the
    surplus is cut at the ceiling rather than the paid answer thrown away."""
    products = normalize_products(
        {"products": [product(f"商品{i}") for i in range(20)]}, products_per_scene=4)

    assert len(products) == 4 + PRODUCT_SLACK


def test_a_product_list_that_misses_the_order_of_magnitude_is_rejected() -> None:
    with pytest.raises(ValueError, match="unexpected number of products"):
        validate_products({"model": MODEL, "products": [product()]}, products_per_scene=16)


def test_the_same_product_named_twice_is_kept_once() -> None:
    """One role is one retrieval and one verdict call, so a repeated name costs
    twice and shows the operator the same row twice."""
    products = normalize_products(
        {"products": [product("帐篷"), product("帐篷"), product("睡袋")]},
        products_per_scene=10,
    )

    assert [item["product_cn"] for item in products] == ["帐篷", "睡袋"]


# ---------- the conclusions ----------

def test_a_rejected_summary_says_which_part_was_wrong() -> None:
    """It fired once in production and the blanket message left nothing to act on,
    and it never reproduced. Naming the condition is what makes the next one usable."""
    value = synthesis()
    value["manager_summary"]["recommended_actions"] = ["一", "二"]

    with pytest.raises(ValueError, match="2 recommended_actions"):
        validate_synthesis(value)


def test_the_retired_field_is_dropped_rather_than_stored(monkeypatch) -> None:
    """An answer that still carries it would otherwise reach the browser and get
    written into the artifact, which is how a removed field comes back."""
    value = synthesis()
    value["store_profile"] = section(actionable_implication="对我们意义")
    value["future_product_structure"] = {
        **section(actionable_implication="对我们意义"), "priority_order": ["甲", "乙", "丙"]}
    serve(monkeypatch, response(json.dumps(value, ensure_ascii=False)))

    result, _ = analyze_synthesis({"store": {}}, "secret-key")

    assert result["store_profile"] == {"judgement": "判断", "evidence": "依据"}
    assert "actionable_implication" not in result["future_product_structure"]


def test_the_reading_is_written_before_there_is_any_scene_to_summarise(monkeypatch) -> None:
    """What a store makes its money on is the reason the scenes get written, so
    the call that decides it cannot be handed the scenes: that would make it a
    summary of them, which is the ordering this round exists to undo."""
    sent = record(monkeypatch, answer=synthesis())

    analyze_synthesis({"store": {}}, "secret-key")

    ask = json.loads(sent[0]["messages"][1]["content"])
    assert "scenes" not in ask
    assert "store_conclusion" not in ask


def test_the_scene_call_is_handed_the_reading_it_builds_on(monkeypatch) -> None:
    sent = record(monkeypatch)

    analyze_scenes({"store": {}}, "secret-key", scene_count=1, conclusion=synthesis())

    ask = json.loads(sent[0]["messages"][1]["content"])
    assert ask["store_conclusion"]["current_product_structure"] == section()


# ---------- failure the operator has to be able to act on ----------

def test_a_truncated_answer_is_named_as_such(monkeypatch) -> None:
    serve(monkeypatch, response(
        '{"model":"deepseek-flash","scenes":[{"scene_name":"半截',
        finish_reason="length", completion_tokens=MAX_TOKENS,
    ))

    with pytest.raises(RuntimeError, match="截断"):
        analyze_scenes({"store": {}}, "secret-key", scene_count=8)


def test_content_that_is_not_json_at_all_says_so_in_chinese(monkeypatch) -> None:
    serve(monkeypatch, response("这不是 JSON"))

    with pytest.raises(RuntimeError, match="读不出来"):
        analyze_scenes({"store": {}}, "secret-key")


def test_an_unreadable_answer_blames_the_temperature_when_it_is_high(monkeypatch) -> None:
    """Six runs out of six at temperature 2.0 came back as word salad with invented
    field names. The operator needs to be told that is what a hot setting does,
    rather than told to rerun the same setting and lose another paid call."""
    serve(monkeypatch, response("平置三层复叠 30差各家庭，你需要"))

    with pytest.raises(RuntimeError, match="天马行空程度"):
        analyze_scenes({"store": {}}, "secret-key", temperature=2.0)


def test_a_cool_answer_does_not_get_blamed_on_the_temperature(monkeypatch) -> None:
    serve(monkeypatch, response("这不是 JSON"))

    with pytest.raises(RuntimeError) as caught:
        analyze_scenes({"store": {}}, "secret-key", temperature=0.2)

    assert "温度" not in str(caught.value)


def test_a_good_answer_comes_back_with_its_usage(monkeypatch) -> None:
    serve(monkeypatch, response(json.dumps({"model": MODEL, "scenes": [scene()]}, ensure_ascii=False)))

    result, receipt = analyze_scenes({"store": {}}, "secret-key", scene_count=1)

    assert result["scenes"][0]["scene_name"] == "周末露营"
    assert receipt["response_id"] == "chat-1"
    assert receipt["usage"]["total_tokens"] == 200
    assert "secret-key" not in json.dumps(receipt, ensure_ascii=False)


def test_the_ruled_out_clues_travel_labelled_rather_than_hidden(monkeypatch) -> None:
    """Excluding is not deleting. Every call is handed the ruled-out products and
    told that they are ruled out, so the model knows what not to write; dropping
    them from the input would leave it free to invent them back."""
    sent = record(monkeypatch)

    source = {"store": {"store_name": "店"},
              "observed_product_clues": [{"clue": "本店主营的商品"}],
              "excluded_product_clues": [{"clue": "被运营排除的商品"}]}
    analyze_scenes(source, "secret-key", scene_count=1)
    analyze_scene_products(source, scene(), "secret-key", products_per_scene=2)

    assert sent, "the call must actually have been made"
    for payload in sent:
        ask = json.loads(payload["messages"][1]["content"])
        assert [item["clue"] for item in ask["excluded_product_clues"]] == ["被运营排除的商品"]
        assert [item["clue"] for item in ask["observed_product_clues"]] == ["本店主营的商品"]


# ---------- putting the three back together ----------

def test_the_three_calls_are_reassembled_in_the_order_the_scenes_came_back() -> None:
    scenes = {"model": MODEL, "scenes": [scene("甲"), scene("乙"), scene("丙")]}
    products = [
        {"scene_name": "丙", "products": [product("丙的货")]},
        {"scene_name": "甲", "products": [product("甲的货")]},
    ]

    result = assemble(scenes, products, synthesis())

    assert [item["scene_name"] for item in result["scenes"]] == ["甲", "乙", "丙"]
    assert result["scenes"][0]["product_needs"][0]["product_cn"] == "甲的货"
    # A scene whose product call never ran has no roles, rather than another
    # scene's roles.
    assert result["scenes"][1]["product_needs"] == []


def test_the_assembled_document_passes_the_same_check_the_old_one_did() -> None:
    scenes = {"model": MODEL, "scenes": [scene("甲"), scene("乙")]}
    products = [{"scene_name": name, "products": [product(f"{name}的货"), product("通用货")]}
                for name in ("甲", "乙")]

    result = assemble(scenes, products, synthesis())

    validate(result, scene_count=2, products_per_scene=4)
    assert result["model"] == MODEL


def test_the_temperature_reaches_the_payload(monkeypatch) -> None:
    """Temperature is the only sampling knob left. The two penalties were taken
    off the form and out of the payload after DeepSeek marked them deprecated:
    "该参数已不再支持。传入该参数将不会产生任何效果。" Sending them would be
    sending a setting the operator cannot set and the API will not read."""
    sent = record(monkeypatch)

    analyze_scenes({"store": {}}, "secret-key", scene_count=1, temperature=0.7)

    assert sent[0]["temperature"] == 0.7
    assert "frequency_penalty" not in sent[0]
    assert "presence_penalty" not in sent[0]


# ---------- expansions keep their own shape ----------

def test_expansions_still_cap_each_language_at_the_operators_count(monkeypatch) -> None:
    serve(monkeypatch, response(json.dumps({"model": MODEL, "scenes": [{
        "scene_name": "露营",
        "products": [{"product_cn": "帐篷", "product_en": "Tent", "canonical_cn": "帐篷",
                      "canonical_en": "Tent",
                      "expanded_cn": ["a", "b", "c", "d"], "expanded_en": ["a", "b", "c", "d"]}],
    }]}, ensure_ascii=False)))

    result, _ = analyze_expansions(
        {"scenes": [{"scene_name": "露营",
                     "products": [{"product_cn": "帐篷", "product_en": "Tent"}]}]},
        "secret-key", expansion_terms=2,
    )

    assert result["scenes"][0]["products"][0]["expanded_cn"] == ["a", "b"]


def test_asking_for_no_expansions_is_not_a_reason_to_call_the_model(monkeypatch) -> None:
    """Zero means the search runs on the product's own two names. A call that
    could only hand back the empty list it was told to produce is money spent to
    make the search wider than the operator wanted it."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("no expansion call should have been made")

    monkeypatch.setattr("urllib.request.urlopen", refuse)

    result, receipt = analyze_expansions(
        {"scenes": [{"scene_name": "露营",
                     "products": [{"product_cn": "帐篷", "product_en": "Tent"}]}]},
        "secret-key", expansion_terms=0,
    )

    assert result["scenes"][0]["products"][0] == {
        "product_cn": "帐篷", "product_en": "Tent", "canonical_cn": "帐篷",
        "canonical_en": "Tent", "expanded_cn": [], "expanded_en": [],
    }
    assert receipt["expansion_fallback"]["used"] is False
