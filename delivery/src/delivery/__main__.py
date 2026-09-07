"""Package entry point so `python -m delivery` runs the CLI.

`main` returns an int exit code (0 success, 1 delivery failure with an
`ERROR <code>` diagnostic on stderr, 2 argparse usage errors); sys.exit
propagates it as the process exit status.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
