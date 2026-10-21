"""Compatibility entry point for the current, local-only publisher."""
from publish_local import main

if __name__ == '__main__':
    raise SystemExit(main())
