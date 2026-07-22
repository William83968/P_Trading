from pirate_trading import __version__
from pirate_trading.__main__ import main


def test_package_version() -> None:
    assert __version__ == "0.1.0"


def test_main_prints_package_information(capsys) -> None:
    assert main() == 0
    assert capsys.readouterr().out == "Pirate Trading 0.1.0\n"
