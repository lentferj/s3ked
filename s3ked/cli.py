# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  s3ked contributors
#
# This file is part of s3ked.
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.

"""``s3kcli`` -- a read-mostly command-line explorer for the sampler.

Deliberately narrower than the TUI. The destructive operations the protocol
offers are **not** exposed here: a shell is exactly the place where a typo or
a recalled history line fires something irreversible, and this family's
deletes have no device-side confirmation. They live behind the TUI's
arm-then-fire screen instead.

``set`` is the one write, and it is gated behind ``--allow-write``.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional

from s3k import params as p

__all__ = ["main", "build_parser"]


def _fmt_table(rows: List[List[str]], headers: List[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    out = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    out.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        out.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(out)


# --- commands ---------------------------------------------------------------


def _cmd_ports(_bridge, _args) -> None:
    from s3k.bridge import list_ports

    ins, outs = list_ports()
    for label, names in (("inputs", ins), ("outputs", outs)):
        print(f"{label}:")
        for name in names:
            print(f"  {name}")
        if not names:
            print("  (none)")


def _cmd_status(bridge, _args) -> None:
    status = bridge.status()
    print(f"connection          {bridge.description}")
    print(f"software version    {status.version}")
    print(f"exclusive channel   {status.exclusive_channel}")
    print(
        f"header blocks       {status.used_blocks} used, "
        f"{status.free_blocks} free of {status.max_blocks}"
    )
    print(
        f"sample words        {status.used_words} used, "
        f"{status.free_words} free of {status.max_words}"
    )


def _cmd_programs(bridge, _args) -> None:
    names = bridge.program_list()
    if not names:
        print("no resident programs")
        return
    print(_fmt_table([[str(i), n] for i, n in enumerate(names)], ["num", "name"]))


def _cmd_samples(bridge, _args) -> None:
    names = bridge.sample_list()
    if not names:
        print("no resident samples")
        return
    print(_fmt_table([[str(i), n] for i, n in enumerate(names)], ["num", "name"]))


def _cmd_header(bridge, args) -> None:
    values = bridge.get_header(
        args.region, args.index, keygroup=args.keygroup
    )
    rows = []
    for param in p.region_params(args.region):
        if args.group and not (
            param.group == args.group or param.group.startswith(args.group + ".")
        ):
            continue
        rows.append(
            [
                str(param.offset),
                param.name,
                param.group,
                p.describe_value(param, values[param.name]),
            ]
        )
    if not rows:
        print(f"no parameters in group {args.group!r}")
        return
    print(_fmt_table(rows, ["off", "name", "group", "value"]))


def _cmd_get(bridge, args) -> None:
    param = p.lookup(args.name, args.region)
    value = bridge.get_parameter(param, args.index, keygroup=args.keygroup)
    print(f"{param.name} = {p.describe_value(param, value)}  (raw {value!r})")
    if param.notes:
        print(f"  note: {param.notes}")


def _cmd_set(bridge, args) -> None:
    if not args.allow_write:
        raise ValueError(
            "writing is disabled; pass --allow-write if you mean it "
            "(and read DISCLAIMER.md first -- these byte offsets are "
            "unverified against hardware)"
        )
    param = p.lookup(args.name, args.region)
    value: object = args.value
    if param.kind != "text":
        try:
            value = int(args.value, 0)
        except ValueError:
            raise ValueError(
                f"{param.name} is numeric; {args.value!r} is not a number"
            ) from None
    bridge.set_parameter(param, args.index, value, keygroup=args.keygroup)
    read_back = bridge.get_parameter(param, args.index, keygroup=args.keygroup)
    print(f"{param.name} = {p.describe_value(param, read_back)}")


def _cmd_params(_bridge, args) -> None:
    """List the parameter table itself. Needs no device."""
    rows = []
    for param in p._PARAMS:
        if args.region and param.region != args.region:
            continue
        if args.group and not (
            param.group == args.group or param.group.startswith(args.group + ".")
        ):
            continue
        if args.search and args.search.upper() not in param.name.upper():
            continue
        rows.append(
            [
                param.region,
                str(param.offset),
                param.name,
                param.group,
                f"{param.minimum}..{param.maximum}" if param.kind == "num" else param.kind,
                param.unit or "",
            ]
        )
    if not rows:
        print("no parameters match")
        return
    print(_fmt_table(rows, ["region", "off", "name", "group", "range", "unit"]))


def _cmd_groups(_bridge, args) -> None:
    for name in p.groups(args.region):
        print(f"{name}  ({len(p.group_params(name))})")


#: Commands that need no device at all, so they never build a bridge.
_OFFLINE: Dict[str, Callable] = {
    "ports": _cmd_ports,
    "params": _cmd_params,
    "groups": _cmd_groups,
}

_COMMANDS: Dict[str, Callable] = {
    **_OFFLINE,
    "status": _cmd_status,
    "programs": _cmd_programs,
    "samples": _cmd_samples,
    "header": _cmd_header,
    "get": _cmd_get,
    "set": _cmd_set,
}


# --- argument parsing -------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="s3kcli",
        description="Explore an Akai S1000/S3000-family sampler over MIDI SysEx.",
        epilog="Destructive operations are intentionally not available here; "
        "use the s3ked TUI, which requires an explicit arm-then-fire step.",
    )
    parser.add_argument("--port", help="MIDI port name (default: autodetect)")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="run against the built-in demo sampler; opens no MIDI ports",
    )
    parser.add_argument(
        "--exclusive-channel",
        type=int,
        default=None,
        help="the device's SysEx exclusive channel (default: 0, the factory value)",
    )
    parser.add_argument("--timeout", type=float, default=None, help="reply timeout, seconds")
    parser.add_argument("--config", default=None, help="path to config.toml")
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="permit `set` to write to the device",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ports", help="list MIDI ports on this host")
    sub.add_parser("status", help="RSTAT: version, memory, exclusive channel")
    sub.add_parser("programs", help="list resident program names")
    sub.add_parser("samples", help="list resident sample names")

    def _add_target(sp, *, with_group: bool = False) -> None:
        sp.add_argument("index", type=int, help="program or sample number")
        sp.add_argument(
            "--keygroup", type=int, default=0, help="keygroup number (keygroup region)"
        )
        if with_group:
            sp.add_argument("--group", default=None, help="limit to a dotted group")

    sp = sub.add_parser("header", help="dump one whole header, decoded")
    sp.add_argument("region", choices=p.REGIONS)
    _add_target(sp, with_group=True)

    sp = sub.add_parser("get", help="read one parameter")
    sp.add_argument("name", help="parameter name, e.g. PRIORT")
    _add_target(sp)
    sp.add_argument("--region", choices=p.REGIONS, default=None)

    sp = sub.add_parser("set", help="write one parameter (needs --allow-write)")
    sp.add_argument("name", help="parameter name")
    sp.add_argument("value", help="new value")
    _add_target(sp)
    sp.add_argument("--region", choices=p.REGIONS, default=None)

    sp = sub.add_parser("params", help="list the parameter table (no device needed)")
    sp.add_argument("--region", choices=p.REGIONS, default=None)
    sp.add_argument("--group", default=None)
    sp.add_argument("--search", default=None, help="substring of the parameter name")

    sp = sub.add_parser("groups", help="list parameter groups (no device needed)")
    sp.add_argument("--region", choices=p.REGIONS, default=None)

    return parser


def _build_bridge(args):
    from s3k import bridge as b

    if args.demo:
        from s3ked.demo import DemoBridge

        return DemoBridge()

    kwargs = {}
    if args.timeout is not None:
        kwargs["timeout"] = args.timeout
    channel = args.exclusive_channel

    if args.port:
        return b.S3kBridge.standard(
            args.port,
            exclusive_channel=(
                channel if channel is not None else 0
            ),
            **kwargs,
        )

    channels = (channel,) if channel is not None else (0,)
    return b.S3kBridge.autodetect(
        channels=channels,
        config_path=args.config or b.DEFAULT_CONFIG_PATH,
        on_try=lambda name: print(f"  probing {name}...", file=sys.stderr),
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    handler = _COMMANDS[args.command]

    if args.command in _OFFLINE:
        try:
            handler(None, args)
        except (LookupError, ValueError) as exc:
            sys.exit(f"error: {exc}")
        return 0

    try:
        bridge = _build_bridge(args)
    except Exception as exc:
        sys.exit(f"error: {exc}")

    try:
        handler(bridge, args)
    except (LookupError, TimeoutError, ValueError, RuntimeError) as exc:
        sys.exit(f"error: {exc}")
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
