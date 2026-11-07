import json

import pytest

from screen_prove.cli import main
from screen_prove.contracts import read_json
from screen_prove.risk_of_bias import verify_rob_assessment
from screen_prove.synthesis import verify_calculation


def test_cli_init_creates_minimal_protocol_files(tmp_path):
    assert main(["init", str(tmp_path)]) == 0

    assert (tmp_path / "protocol.json").exists()
    assert (tmp_path / "procedure.json").exists()
    assert (tmp_path / "schema.json").exists()


def test_cli_end_to_end_offline_screen_and_replay(tmp_path):
    main(["init", str(tmp_path)])
    source = tmp_path / "refs.ris"
    source.write_text("TY  - JOUR\nTI  - Alpha\nC5  - 1\nER  -\n", encoding="utf-8")
    corpus = tmp_path / "corpus.json"
    main(["import", str(source), "--source", "published", "--output", str(corpus), "--raw-dir", str(tmp_path / "raw")])
    lock = tmp_path / "lock.json"
    main(["lock", "--protocol", str(tmp_path / "protocol.json"), "--procedure", str(tmp_path / "procedure.json"), "--schema", str(tmp_path / "schema.json"), "--input", str(corpus), "--output", str(lock)])
    dedup = tmp_path / "dedup.json"
    main(["dedup", str(corpus), "--output", str(dedup)])
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"record:placeholder": "include"}), encoding="utf-8")
    screening = tmp_path / "screening.json"
    # The unknown decision map intentionally produces an explicit abstain.
    main(["screen", str(dedup), "--lock", str(lock), "--decisions", str(decisions), "--cache-dir", str(tmp_path / "cache"), "--output", str(screening), "--human-adjudication", "off"])
    package = tmp_path / "package.json"
    main(["package", str(screening), "--output", str(package)])
    assert read_json(package)["package_type"] == "AI_ONLY_SCREENING_PACKAGE"
    assert main(["replay", str(package), "--lock", str(lock), "--dedup", str(dedup), "--cache-dir", str(tmp_path / "cache")]) == 0


def test_cli_extract_builds_source_anchored_record(tmp_path):
    source = tmp_path / "full-text.txt"
    source.write_text("Participants: 42\n", encoding="utf-8")
    fields = tmp_path / "fields.json"
    fields.write_text(
        json.dumps(
            [
                {"field": "sample_size", "start": 14, "end": 16},
                {"field": "primary_outcome", "status": "not_reported"},
                {
                    "field": "estimated_rate",
                    "status": "estimated",
                    "value": "0.25",
                    "estimation_note": "ESTIMATED: 1 of 4 participants",
                },
            ]
        ),
        encoding="utf-8",
    )
    output = tmp_path / "extraction.json"

    assert main(
        [
            "extract",
            str(source),
            "--record-id",
            "record-1",
            "--provenance-file",
            "full-text.txt",
            "--fields",
            str(fields),
            "--output",
            str(output),
        ]
    ) == 0

    extracted = read_json(output)
    assert extracted["record_id"] == "record-1"
    assert [field["status"] for field in extracted["fields"]] == [
        "reported",
        "not_reported",
        "estimated",
    ]
    assert extracted["fields"][0]["span"]["quote"] == "42"


@pytest.mark.parametrize(
    "entry",
    [
        {"field": "sample_size", "status": "not_reported", "start": 0},
        {"field": "sample_size", "status": "not_reported", "value": "42"},
        {"field": "sample_size", "status": "estimated", "start": 0, "end": 2},
        {"field": "sample_size", "status": "estimated", "value": "42", "extra": True},
    ],
)
def test_cli_extract_rejects_status_contradictory_fields(tmp_path, entry):
    source = tmp_path / "full-text.txt"
    source.write_text("Participants: 42\n", encoding="utf-8")
    fields = tmp_path / "fields.json"
    fields.write_text(json.dumps([entry]), encoding="utf-8")

    with pytest.raises(ValueError, match="not allowed"):
        main(
            [
                "extract",
                str(source),
                "--record-id",
                "record-1",
                "--provenance-file",
                "full-text.txt",
                "--fields",
                str(fields),
                "--output",
                str(tmp_path / "extraction.json"),
            ]
        )


def test_cli_rob_builds_hash_bound_assessment(tmp_path):
    spec = tmp_path / "rob.json"
    spec.write_text(
        json.dumps(
            {
                "record_id": "record-1",
                "tool": "protocol-rob-v1",
                "domains": [
                    {
                        "domain": "randomization_process",
                        "judgment": "low_risk",
                        "rationale": "sequence generation is reported",
                        "source_links": ["Methods p.2"],
                    },
                    {
                        "domain": "missing_outcome_data",
                        "judgment": "not_assessed",
                        "rationale": "not reported",
                    },
                ],
                "overall_judgment": "some_concerns",
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "rob-output.json"

    assert main(["rob", str(spec), "--output", str(output)]) == 0

    assessment = read_json(output)
    assert assessment["version"] == "rob-assessment-v1"
    assert assessment["domains"][1]["source_links"] == []
    assert assessment["rob_hash"]
    assert verify_rob_assessment(assessment)["record_id"] == "record-1"


def test_cli_rob_rejects_unsupported_spec_fields(tmp_path):
    spec = tmp_path / "rob.json"
    spec.write_text(
        json.dumps(
            {
                "record_id": "record-1",
                "tool": "protocol-rob-v1",
                "domains": [],
                "overall_judgment": "not_assessed",
                "inference": "do not accept this",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported fields"):
        main(["rob", str(spec), "--output", str(tmp_path / "output.json")])


def test_cli_synthesize_builds_verifiable_single_study_calculation(tmp_path):
    spec = tmp_path / "synthesis.json"
    spec.write_text(
        json.dumps(
            {
                "record_id": "record-1",
                "measure": "risk_difference",
                "counts": {
                    "treated_events": 2,
                    "treated_total": 10,
                    "control_events": 1,
                    "control_total": 10,
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "synthesis-output.json"

    assert main(["synthesize", str(spec), "--output", str(output)]) == 0

    calculation = read_json(output)
    assert calculation["status"] == "calculated"
    assert calculation["measure"] == "risk_difference"
    assert verify_calculation(calculation)["point_estimate"] == calculation["point_estimate"]


def test_cli_synthesize_preserves_insufficient_data_for_zero_event_ratio(tmp_path):
    spec = tmp_path / "synthesis.json"
    spec.write_text(
        json.dumps(
            {
                "record_id": "record-1",
                "measure": "risk_ratio",
                "counts": {
                    "treated_events": 0,
                    "treated_total": 10,
                    "control_events": 1,
                    "control_total": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "synthesis-output.json"
    assert main(["synthesize", str(spec), "--output", str(output)]) == 0
    calculation = read_json(output)
    assert calculation["status"] == "insufficient_data"
    assert calculation["point_estimate"] is None
    assert "zero_event_cell_ratio_measure_undefined" in calculation["uncertainty_flags"]


def test_cli_synthesize_rejects_unsupported_count_fields(tmp_path):
    spec = tmp_path / "synthesis.json"
    spec.write_text(
        json.dumps(
            {
                "record_id": "record-1",
                "measure": "risk_difference",
                "counts": {
                    "treated_events": 2,
                    "treated_total": 10,
                    "control_events": 1,
                    "control_total": 10,
                    "continuity_correction": 0.5,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported fields"):
        main(["synthesize", str(spec), "--output", str(tmp_path / "output.json")])
