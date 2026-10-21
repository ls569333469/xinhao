"""Compatibility entry point: refresh saved reserve blocks only."""
from publish_local import main

if __name__ == '__main__':
    raise SystemExit(main('reserves'))
