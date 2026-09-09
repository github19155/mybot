"""Architecture contract coverage for WorkAgent documentation."""

from importlib.resources import files as pkg_files


def test_tool_contract_defines_three_lane_worker_routing() -> None:
    contract = (
        pkg_files("nanobot") / "templates" / "agent" / "tool_contract.md"
    ).read_text(encoding="utf-8")

    assert "three lanes" in contract
    assert "Specialist" in contract
    assert "WorkAgent" in contract
    assert "permanent `general`" in contract
    assert "role omitted" in contract
    assert "any explicit run override means WorkAgent" in contract
    assert "does not inherit General's persistent" in contract
    assert "Children cannot create further Subagents" in contract
