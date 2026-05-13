"""Enable `python -m cathedral.v2.cli ...`"""

import sys

from cathedral.v2.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
