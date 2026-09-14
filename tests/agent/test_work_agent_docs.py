"""Architecture contract coverage for WorkAgent documentation."""

from importlib.resources import files as pkg_files


def test_tool_contract_defines_worker_routing() -> None:
    contract = (
        pkg_files("nanobot") / "templates" / "agent" / "tool_contract.md"
    ).read_text(encoding="utf-8")

    assert "Prefer a matching active Specialist" in contract
    assert "Use a WorkAgent" in contract
    assert "special tools, prompt, model, or runtime configuration" in contract
    assert "permanent General" in contract
    assert "Children do not create further Workers" in contract
