"""Tests for shared LLM tool helpers."""

from app.llm_tools.shared import coerce_json_args


def test_coerce_unwraps_stringified_changes_array():
    args = {
        "changes": (
            '[{"operation": "update", "match": {"id": {"eq": 42}}, '
            '"relations": {"current_tissues": {"mode": "append_snapshot", '
            '"records": [{"tissue_id": 9, "role": "primary", '
            '"loading_factor": 1.0}]}}}]'
        )
    }
    out = coerce_json_args(args)
    assert isinstance(out["changes"], list)
    assert out["changes"][0]["operation"] == "update"
    assert out["changes"][0]["match"] == {"id": {"eq": 42}}
    rec = out["changes"][0]["relations"]["current_tissues"]["records"][0]
    assert rec["tissue_id"] == 9


def test_coerce_recurses_into_nested_stringified_objects():
    args = {
        "changes": [
            {
                "operation": "update",
                "match": '{"id": {"eq": 7}}',
                "relations": (
                    '{"current_tissues": {"mode": "append_snapshot", '
                    '"records": [{"tissue_id": 1, "role": "primary", '
                    '"loading_factor": 1.0}]}}'
                ),
            }
        ]
    }
    out = coerce_json_args(args)
    change = out["changes"][0]
    assert change["match"] == {"id": {"eq": 7}}
    assert change["relations"]["current_tissues"]["mode"] == "append_snapshot"


def test_coerce_leaves_plain_strings_untouched():
    args = {
        "changes": [
            {
                "operation": "create",
                "set": {
                    "name": "Bench Press",
                    "notes": "Use a spotter for {heavy} sets",
                    "equipment": "[barbell]",
                },
            }
        ],
    }
    out = coerce_json_args(args)
    s = out["changes"][0]["set"]
    assert s["name"] == "Bench Press"
    assert s["notes"] == "Use a spotter for {heavy} sets"
    # `[barbell]` is not valid JSON, so it must be preserved as-is.
    assert s["equipment"] == "[barbell]"


def test_coerce_handles_double_encoded_strings():
    # Some models double-encode: a JSON string containing a JSON string
    # that itself contains the array. coerce_json_args should peel both layers.
    inner = '[{"operation": "delete", "match": {"id": {"eq": 1}}}]'
    args = {"changes": inner}
    out = coerce_json_args(args)
    assert isinstance(out["changes"], list)
    assert out["changes"][0]["operation"] == "delete"


def test_coerce_passes_through_non_dicts():
    assert coerce_json_args(None) is None
    assert coerce_json_args(42) == 42
    assert coerce_json_args("plain") == "plain"
    assert coerce_json_args([1, 2, 3]) == [1, 2, 3]
