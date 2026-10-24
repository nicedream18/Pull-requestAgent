import pytest

from prguard.cli import main


def test_repo_target_requires_pull_request_number():
    with pytest.raises(SystemExit) as exc_info:
        main(["review", "--repo", "acme/widgets"])

    assert exc_info.value.code == 2

