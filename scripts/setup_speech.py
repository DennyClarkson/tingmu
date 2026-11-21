"""Install the speech models in the local application cache."""
import argparse
from pathlib import Path

from tingmu.runtime import cache_root, confined_path
from tingmu.speech_assets import install

if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--cache", type=Path, default=cache_root())
    install(confined_path(cache_root(), cli.parse_args().cache))
