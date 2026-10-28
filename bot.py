"""GitHub Action entry point. The logic lives in theseus.ci; this file stays so that action.yml
(and any workflow that runs `python bot.py` directly) keeps working."""

import sys

from theseus.ci import main

if __name__ == "__main__":
    sys.exit(main())
