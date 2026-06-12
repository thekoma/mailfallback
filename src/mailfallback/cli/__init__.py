"""mfb CLI — admin operations.

Invoked via `docker compose exec mailfallback uv run mfb <subcommand>`.
Uses argparse (no new dependency).
"""

import argparse
import sys


def app() -> int:
    parser = argparse.ArgumentParser(prog="mfb")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="Mail index admin")
    index_sub = p_index.add_subparsers(dest="index_cmd", required=True)
    index_sub.add_parser("status", help="Show rebuild_status per account")
    p_rebuild = index_sub.add_parser(
        "rebuild-account", help="Re-walk a live Maildir into the index"
    )
    p_rebuild.add_argument("account_id")
    p_backfill = index_sub.add_parser(
        "backfill-snapshots", help="Populate snapshot_messages for existing snapshots"
    )
    p_backfill.add_argument("account_id")
    p_backfill_atts = index_sub.add_parser(
        "backfill-attachments", help="Parse attachments for index rows that pre-date them"
    )
    p_backfill_atts.add_argument("account_id")
    p_backfill_atts.add_argument(
        "--content-only",
        action="store_true",
        help="Extract Tika text into rows with NULL content_text (requires tika_enabled)",
    )

    args = parser.parse_args()
    if args.cmd == "index":
        from mailfallback.cli.index import handle_index

        return handle_index(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(app())
