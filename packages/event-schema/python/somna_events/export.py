"""Exports the AgentEvent JSON schema to stdout.

Usage:
    python -m somna_events.export > ../schema/event.schema.json
"""

import sys

from .events import dump_json_schema


def main() -> None:
    sys.stdout.write(dump_json_schema())
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
