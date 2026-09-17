"""Control the internal DEV experiment API without running a second local worker."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soc_agent.demo.corpus_experiment_cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
