"""Enable `python -m cathedral.v3.cli ...`"""

import sys

from cathedral.v3.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
