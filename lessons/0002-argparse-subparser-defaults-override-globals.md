# 0002 — argparse subparser copies of a global flag silently override it

**Date:** 2026-09-13
**Area:** CLI / dev harness

## What happened
`python -m marketing_radar.cli --offline scan --user-id demo` ignored `--offline` and tried to open a real Firestore client (`DefaultCredentialsError`). The flag was parsed, then lost.

## Root cause
To let `--offline` / `--user-id` work both before and after the subcommand, the same options were added to the main parser *and* to every subparser via `parents=[common]`. argparse applies the subparser's defaults (`False` / `None`) after the main parser has already set the value, so any global flag placed before the subcommand was reset.

## Fix
The subparser copies use `default=argparse.SUPPRESS`, so an absent option on the subcommand leaves the main parser's value untouched (`cli.py`, `common(suppress=True)`).

## How to avoid next time
When sharing options between a parent parser and subparsers, always give the subparser copies `default=argparse.SUPPRESS`. Test the CLI with the flag in both positions.
