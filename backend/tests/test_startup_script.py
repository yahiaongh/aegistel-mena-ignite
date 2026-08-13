from pathlib import Path


def test_startup_script_binds_to_all_interfaces_for_container_runtime():
    script = Path(__file__).resolve().parents[1] / "../start.sh"
    content = script.read_text(encoding="utf-8")

    assert "0.0.0.0" in content
    assert "127.0.0.1" not in content
    assert "PORT=" in content
