"""MessageFramework 테스트."""

from __future__ import annotations

from pathlib import Path

from gwanjong_mcp.message import MessageFramework


def _make_fw(tmp_path: Path) -> MessageFramework:
    return MessageFramework(db_path=tmp_path / "test.db")


def test_create_and_get(tmp_path: Path) -> None:
    fw = _make_fw(tmp_path)
    frame = fw.create(
        {
            "campaign_id": "camp_001",
            "persona_segment": "senior-backend",
            "value_prop": "Zero-dep MCP tool routing",
            "proof_points": ["248 tools tested", "72% accuracy"],
            "objections": {"too complex": "It's a single pip install"},
            "hooks": ["LLM에 tool 100개 넣으면 어떻게 될까요?"],
        }
    )

    assert frame.id.startswith("mf_")
    assert frame.persona_segment == "senior-backend"
    assert len(frame.proof_points) == 2

    loaded = fw.get(frame.id)
    assert loaded is not None
    assert loaded.value_prop == "Zero-dep MCP tool routing"


def test_list_by_campaign(tmp_path: Path) -> None:
    fw = _make_fw(tmp_path)
    fw.create({"campaign_id": "camp_001", "persona_segment": "senior"})
    fw.create({"campaign_id": "camp_001", "persona_segment": "junior"})
    fw.create({"campaign_id": "camp_002", "persona_segment": "manager"})

    frames = fw.list_by_campaign("camp_001")
    assert len(frames) == 2


def test_select_hook(tmp_path: Path) -> None:
    fw = _make_fw(tmp_path)
    fw.create(
        {
            "campaign_id": "camp_001",
            "persona_segment": "dev",
            "hooks": ["Did you know MCP can do X?", "Have you tried Y?"],
        }
    )

    hook = fw.select_hook("camp_001")
    assert hook is not None
    assert "MCP" in hook


def test_select_hook_no_frames(tmp_path: Path) -> None:
    fw = _make_fw(tmp_path)
    assert fw.select_hook("nonexistent") is None


def test_get_objection_response(tmp_path: Path) -> None:
    fw = _make_fw(tmp_path)
    fw.create(
        {
            "campaign_id": "camp_001",
            "persona_segment": "skeptic",
            "objections": {
                "too complex": "Single pip install, zero dependencies",
                "not production ready": "Used in production by X teams",
            },
        }
    )

    response = fw.get_objection_response("camp_001", "this seems too complex")
    assert response is not None
    assert "pip install" in response

    # 매칭 안 되는 경우
    assert fw.get_objection_response("camp_001", "pricing concern") is None


def test_get_nonexistent(tmp_path: Path) -> None:
    fw = _make_fw(tmp_path)
    assert fw.get("nonexistent") is None
