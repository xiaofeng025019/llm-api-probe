"""Allow `python -m app <cmd>` (alias for `python -m app.cli <cmd>`)."""

from app.cli import main

raise SystemExit(main())
