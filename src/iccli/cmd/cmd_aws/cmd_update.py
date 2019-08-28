# Copyright 2019 Farzad Senart and Lionel Suss. All rights reserved.
#
# This file is part of IC CLI.
#
# IC CLI is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# IC CLI is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with IC CLI. If not, see <https://www.gnu.org/licenses/>.

import asyncio
import io
import json
import logging
import pathlib
import re
import sys
import weakref
from collections import deque
from typing import (
    IO,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
    Union,
    cast,
)

import click
import treelib
import urwid
from pygments import formatters, highlight, lexers

from ...cloud.aws import config, encode
from ...cloud.aws import util as aws_util
from .. import util as cli_util
from . import load, stack, util

LOGGER = logging.getLogger(__name__)


@click.command(name="update")
@click.option("--params", "overrides", help="Parameter overrides.")
@click.option("--remove", is_flag=True, help="Delete infrastructure.")
@click.option("--yes", is_flag=True, help="Accept changes.")
@click.option("--no-iam", is_flag=True, help="Prevent IAM alteration.")
@click.option("--no-noop", is_flag=True, help="Fail if nothing to update.")
@click.argument("name", metavar="brick", required=False)
@click.argument("version", metavar="version", required=False)
@click.argument("idn", metavar="name", required=False)
@click.pass_context
def cmd(
    ctx,
    name: Optional[str],
    version: Optional[str],
    idn: Optional[str],
    *,
    overrides: Optional[str],
    remove: bool,
    yes: bool,
    no_iam: bool,
    no_noop: bool,
):
    """Update the infrastructure."""
    s3_bucket, s3_prefix = ctx.obj["s3_bucket"], ctx.obj["s3_prefix"]
    if remove:
        if name and version and idn or name and version:
            extra = [a for a in [version, idn] if a]
            raise click.BadArgumentUsage(
                f"Got unexpected extra arguments ({', '.join(extra)})"
            )
        idn, name = name, None
        if not idn:
            raise click.MissingParameter(param_hint='"name"', param_type="argument")
        stk = stack.Stack(idn)
        if not stk.exists:
            raise cli_util.UserError(f"brick does not exist: {idn}")
        tree = _merge(stk.tree)
        state = {"root": stack.State(["DELETE"])}
        for node in tree.all_nodes_itr():
            if "type" in node.data:
                state[node.identifier] = stack.State(["DELETE"])

        def states_itr() -> Iterator[Dict[str, stack.State]]:
            stk.delete(False)
            yield from stk.events(state, False)

        _display(tree, states_itr(), state, yes)
        return
    node, _idn = load.execute(idn, name, version, overrides, s3_bucket, s3_prefix)
    tpl = encode.Template(node)
    body = tpl.dumps()
    tplb = body.encode("utf-8")
    artifacts: List[Tuple[Union[pathlib.Path, IO[bytes]], aws_util.AssetInfo]] = []
    if len(tplb) > 51_000:  # pragma: no cover
        info = aws_util.asset_info(io.BytesIO(tplb))
        body = info.url
        artifacts.append((io.BytesIO(tplb), info))
    # pylint: disable=protected-access
    artifacts += [(a._path, cast(aws_util.AssetInfo, a)) for a in config.ASSETS.get()]
    util.upload(artifacts)
    stk = stack.Stack(_idn)
    tree = _merge(stk.tree if stk.exists else {}, tpl.tree)
    with stk.plan(body, tpl.params, not no_iam) as plan:
        states = iter(plan)
        try:
            state = next(states)
        except StopIteration:
            msg = f"{_idn} is already up to date"
            if no_noop:
                raise cli_util.UserError(msg)
            LOGGER.info(msg)
            return
        _display(tree, states, state, yes)
    click.secho("\nInstance value\n", fg="magenta")
    value = json.dumps(stack.Stack(_idn).value, indent=2, separators=(", ", ": "))
    if sys.stdout.isatty():  # pragma: no cover
        # pylint: disable=no-member
        # PyCQA/pylint#491
        value = highlight(value, lexers.JsonLexer(), formatters.TerminalFormatter())
    click.echo(value, nl=not sys.stdout.isatty())


def _display(
    tree: treelib.Tree,
    states: Iterator[Dict[str, stack.State]],
    state: Mapping[str, stack.State],
    yes: bool,
):
    click.secho("Plan review\n", fg="magenta")
    click.echo(_format_base(_prune(tree, set(state.keys())), state))
    if yes or click.confirm("\nDo you want to continue?"):
        click.secho("Plan accepted", fg="green")
    else:
        click.secho("Plan rejected", fg="red")
        raise click.Abort()
    if sys.stdout.isatty():  # pragma: no cover
        state = _animate(tree, states)
    else:
        state = deque(states, maxlen=1)[0]
    click.secho("\nPlan summary\n", fg="magenta")
    click.echo(_format_base(_prune(tree, set(state.keys())), state))
    logs = _logs(tree, state)
    if logs:
        click.echo()
    for log in logs:
        LOGGER.error(log)
    if logs:
        raise cli_util.UserError("plan execution failed")


def _merge(*info: Mapping) -> treelib.Tree:
    res = treelib.Tree()
    root = res.create_node("plan", "root", None, dict())

    def _process(tree: treelib.Tree, data: Mapping, parent: treelib.Node):
        if not data:
            return
        name, idn = data["name"], data["id"]
        if idn in tree:
            parent = tree[idn]
        else:
            parent = tree.create_node(name, idn, parent, data)
        for child in data.get("children", []):
            _process(tree, child, parent)

    for elem in info:
        _process(res, elem, root)
    return res


def _prune(tree: treelib.Tree, retain: Set[str]) -> treelib.Tree:
    res = treelib.Tree()
    res.create_node(tree[tree.root].tag, tree.root, None, tree[tree.root].data)
    for path in tree.paths_to_leaves():
        if path[-1] in retain:
            for i, node in enumerate(path[1:]):
                if node not in res:
                    curr = tree[node]
                    res.create_node(curr.tag, node, res[path[i]], curr.data)

    return res


def _logs(tree: treelib.Tree, state: Mapping[str, stack.State]) -> Iterable[str]:
    res = []
    for key, val in state.items():
        if val.logs:
            res.extend([f"{tree[key].tag}: {l}" for l in val.logs])
    return res


STATUS_TRANS = dict(
    progress={
        "CREATE_IN_PROGRESS",
        "ROLLBACK_IN_PROGRESS",
        "DELETE_IN_PROGRESS",
        "UPDATE_IN_PROGRESS",
        "UPDATE_COMPLETE_CLEANUP_IN_PROGRESS",
        "UPDATE_ROLLBACK_IN_PROGRESS",
        "UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS",
        "REVIEW_IN_PROGRESS",
    },
    failed={
        "CREATE_FAILED",
        "ROLLBACK_FAILED",
        "DELETE_FAILED",
        "UPDATE_ROLLBACK_FAILED",
        "UPDATE_FAILED",
    },
    complete={
        "CREATE_COMPLETE",
        "DELETE_COMPLETE",
        "UPDATE_COMPLETE",
        "ROLLBACK_COMPLETE",
        "UPDATE_ROLLBACK_COMPLETE",
        "UPDATE",
        "CREATE",
    },
    other={"DELETE_SKIPPED", "PRISTINE", "REPLACE", "DELETE"},
)

OP_TRANS = dict(CREATE="✚", UPDATE="⬆", DELETE="✖", REPLACE="⏏")


def _format_base(tree: treelib.Tree, state: Mapping[str, stack.State]) -> str:
    for node in tree.filter_nodes(lambda n: n.identifier in state):
        nid, data = node.identifier, node.data
        status = state[nid].status[-1]
        color = None
        if status in STATUS_TRANS["progress"]:
            color = "blue"
        elif status in STATUS_TRANS["failed"]:
            color = "red"
        elif status in STATUS_TRANS["complete"]:
            color = "green"
        elif status in STATUS_TRANS["other"]:
            color = "yellow"
        text = OP_TRANS[state[nid].status[0]]
        text += " " + node.tag
        text += " " + click.style(status, fg=color)
        if "type" in data:
            text += " " + click.style(data["type"], dim=True)
        node.tag = text
    return str(tree).strip("\n")


def _format_anim(
    tree: treelib.Tree, state: Mapping[str, stack.State]
) -> Iterable[Union[Tuple[str, str], str]]:  # pragma: no cover
    for node in tree.filter_nodes(lambda n: n.identifier in state):
        nid, data = node.identifier, node.data
        text = OP_TRANS[state[nid].status[0]]
        text += " " + node.tag
        text += " " + state[nid].status[-1]
        if "type" in data:
            text += " " + data["type"]
        node.tag = text
    base = str(tree).strip("\n")
    res: List[Union[Tuple[str, str], str]] = []
    for token in re.split(r"([A-Z_\S]+)", base):
        if token in {"UPDATE", "CREATE", "REPLACE", "DELETE"}:
            res.append(("progress", f"PENDING_{token}"))
        elif token in STATUS_TRANS["progress"]:
            res.append(("progress", token))
        elif token in STATUS_TRANS["failed"]:
            res.append(("failed", token))
        elif token in STATUS_TRANS["complete"]:
            res.append(("complete", token))
        elif token in STATUS_TRANS["other"]:
            res.append(("other", token))
        elif len(token.split(".")) == 3:
            res.append(("type", token))
        else:
            res.append(token)
    return res


def _animate(
    tree: treelib.Tree, states: Iterator[Dict[str, stack.State]]
) -> Dict[str, stack.State]:  # pragma: no cover
    palette = [
        ("progress", "light blue", ""),
        ("failed", "light red", ""),
        ("complete", "light green", ""),
        ("other", "yellow", ""),
        ("type", "dark gray", ""),
        ("title", "light magenta", ""),
    ]

    async_loop = asyncio.get_event_loop()
    state: Dict[str, stack.State] = {}

    def _update(widget_ref: urwid.Text):
        nonlocal state
        widget = widget_ref()
        if not widget:
            raise urwid.ExitMainLoop
        try:
            state = next(states)
        except StopIteration:
            raise urwid.ExitMainLoop
        data: List[Union[Tuple[str, str], str]]
        data = [("title", "Plan execution\n\n")]
        data.extend(_format_anim(_prune(tree, set(state.keys())), state))
        logs = _logs(tree, state)
        if logs:
            data.append(("title", "\n\nLogs\n\n"))
            data.append("\n".join(logs))
        widget.set_text(data)
        async_loop.call_later(1, _update, widget_ref)

    text = urwid.Text("")
    _update(weakref.ref(text))
    widget = urwid.Filler(text, "top")
    event_loop = urwid.AsyncioEventLoop(loop=async_loop)
    loop = urwid.MainLoop(widget, palette, event_loop=event_loop)
    loop.run()
    return state
