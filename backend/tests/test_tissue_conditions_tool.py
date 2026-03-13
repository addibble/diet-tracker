"""Tests for the set_tissue_conditions LLM tool handler, including backdating."""
from datetime import UTC, datetime

import pytest

from app.llm_tools.workout import handle_set_tissue_conditions
from app.models import Tissue


@pytest.fixture()
def tissue(session):
    t = Tissue(name="supraspinatus_tendon", display_name="Supraspinatus Tendon")
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def test_set_tissue_conditions_defaults_to_now(tissue, session):
    before = datetime.now(UTC)
    result = handle_set_tissue_conditions(
        {
            "changes": [
                {
                    "operation": "create",
                    "set": {
                        "tissue_name": "Supraspinatus Tendon",
                        "status": "injured",
                        "severity": 3,
                    },
                }
            ]
        },
        session,
    )
    after = datetime.now(UTC)

    assert result["created_count"] == 1
    created_at = datetime.fromisoformat(result["matches"][0]["created_at"])
    assert before <= created_at <= after


def test_set_tissue_conditions_backdates_with_date_string(tissue, session):
    result = handle_set_tissue_conditions(
        {
            "changes": [
                {
                    "operation": "create",
                    "set": {
                        "tissue_name": "Supraspinatus Tendon",
                        "status": "injured",
                        "severity": 3,
                        "created_at": "2026-02-05",
                    },
                }
            ]
        },
        session,
    )

    assert result["created_count"] == 1
    created_at = datetime.fromisoformat(result["matches"][0]["created_at"])
    assert created_at.year == 2026
    assert created_at.month == 2
    assert created_at.day == 5


def test_set_tissue_conditions_backdates_with_datetime_string(tissue, session):
    result = handle_set_tissue_conditions(
        {
            "changes": [
                {
                    "operation": "create",
                    "set": {
                        "tissue_name": "Supraspinatus Tendon",
                        "status": "rehabbing",
                        "severity": 2,
                        "created_at": "2026-03-01T09:30:00",
                    },
                }
            ]
        },
        session,
    )

    assert result["created_count"] == 1
    created_at = datetime.fromisoformat(result["matches"][0]["created_at"])
    assert created_at.year == 2026
    assert created_at.month == 3
    assert created_at.day == 1
    assert created_at.hour == 9
    assert created_at.minute == 30


def test_set_tissue_conditions_multiple_backdated_entries(tissue, session):
    result = handle_set_tissue_conditions(
        {
            "changes": [
                {
                    "operation": "create",
                    "set": {
                        "tissue_name": "Supraspinatus Tendon",
                        "status": "injured",
                        "severity": 3,
                        "created_at": "2026-02-05",
                    },
                },
                {
                    "operation": "create",
                    "set": {
                        "tissue_name": "Supraspinatus Tendon",
                        "status": "rehabbing",
                        "severity": 3,
                        "created_at": "2026-03-01",
                    },
                },
            ]
        },
        session,
    )

    assert result["created_count"] == 2
    dates = [
        datetime.fromisoformat(m["created_at"]).date().isoformat()
        for m in result["matches"]
    ]
    assert "2026-02-05" in dates
    assert "2026-03-01" in dates


def test_set_tissue_conditions_invalid_created_at_returns_error(tissue, session):
    result = handle_set_tissue_conditions(
        {
            "changes": [
                {
                    "operation": "create",
                    "set": {
                        "tissue_name": "Supraspinatus Tendon",
                        "status": "injured",
                        "severity": 3,
                        "created_at": "not-a-date",
                    },
                }
            ]
        },
        session,
    )

    assert result.get("error") is not None
    assert "created_at" in result["error"].lower() or "invalid" in result["error"].lower()
