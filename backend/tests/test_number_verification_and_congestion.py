import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.tools import get_congestion_insights, verify_number


def _run(tool_obj, *args, **kwargs):
    return json.loads(tool_obj.run(*args, **kwargs))


def test_verify_number_sandbox_documented_numbers():
    # The fraud-test subscriber fails silent identity verification; the clean
    # subscriber passes; undocumented numbers degrade to UNKNOWN (never a
    # silent "verified").
    failed = _run(verify_number, "+99999991000")
    assert failed["verificationStatus"] == "FAILED"
    assert failed["verified"] is False

    passed = _run(verify_number, "+99999991001")
    assert passed["verificationStatus"] == "VERIFIED"
    assert passed["verified"] is True

    unknown = _run(verify_number, "+9999123456")
    assert unknown["verificationStatus"] == "UNKNOWN"
    assert unknown["verified"] is None


def test_congestion_insights_sandbox_documented_numbers():
    high = _run(get_congestion_insights, "+99999991000")
    assert high["maxCongestionLevel"] == "High"
    assert high["congestionLevels"][0]["confidenceLevel"] == 95

    medium = _run(get_congestion_insights, "+99999991002")
    assert medium["maxCongestionLevel"] == "Medium"

    clean = _run(get_congestion_insights, "+99999991001")
    assert clean["maxCongestionLevel"] == "Low"