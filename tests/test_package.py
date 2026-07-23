import pytest

from pirate_trading import __version__
from pirate_trading.__main__ import main


def test_package_version() -> None:
    assert __version__ == "0.1.0"


def test_version_flag(capsys) -> None:
    with pytest.raises(SystemExit, match="0"):
        main(["--version"])
    assert capsys.readouterr().out == "pytest 0.1.0\n"
