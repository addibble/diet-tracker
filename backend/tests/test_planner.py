from app.models import Exercise
from app.planner import _prescribe_all


def test_prescribe_all_normalizes_suitability_score(session):
    exercise = Exercise(name="Test Press", equipment="barbell")
    session.add(exercise)
    session.commit()

    prescribed = _prescribe_all(
        session,
        [
            {
                "id": exercise.id,
                "name": exercise.name,
                "suitability_score": 70,
                "recommendation": "good",
                "weighted_risk_7d": 0.0,
            }
        ],
        [],
    )

    assert prescribed[0]["rep_scheme"] == "volume"


def test_prescribe_all_caps_caution_exercises_out_of_heavy_scheme(session):
    exercise = Exercise(name="Cautious Press", equipment="barbell")
    session.add(exercise)
    session.commit()

    prescribed = _prescribe_all(
        session,
        [
            {
                "id": exercise.id,
                "name": exercise.name,
                "suitability_score": 95,
                "recommendation": "caution",
                "weighted_risk_7d": 30.0,
            }
        ],
        [],
    )

    assert prescribed[0]["rep_scheme"] == "volume"
