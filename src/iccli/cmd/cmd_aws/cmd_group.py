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

import logging
from typing import Optional

import click

from ...cloud.aws import config as aws_config
from .. import config, util
from . import cmd_dump, cmd_id, cmd_setup, cmd_tree, cmd_update, cmd_value
from . import util as aws_util

ALIASES = dict(up="update")


class AliasedGroup(click.Group):
    def get_command(self, ctx, cmd_name):
        orig = super().get_command(ctx, cmd_name)
        if orig is not None:
            return orig
        if cmd_name in ALIASES:
            return super().get_command(ctx, ALIASES[cmd_name])
        return None

    def format_commands(self, ctx, formatter):
        rows = []

        sub_commands = self.list_commands(ctx)

        limit = formatter.width - 6 - max(len(sub_cmd[0]) for sub_cmd in sub_commands)

        for sub_command in sub_commands:
            command = self.get_command(ctx, sub_command)
            if command is None:
                continue
            if hasattr(command, 'hidden') and command.hidden:
                continue

            inverted_aliases = dict()
            for key, value in ALIASES.items():
                inverted_aliases.setdefault(value, list()).append(key)

            if sub_command in inverted_aliases:
                vals = inverted_aliases[sub_command]
                sub_command = f'{sub_command} {{{", ".join(vals)}}}'

            cmd_help = command.get_short_help_str(limit)

            rows.append((sub_command, cmd_help))

        if rows:
            with formatter.section('Commands'):
                formatter.write_dl(rows)


@click.group(name="aws", cls=AliasedGroup)
@click.option("--profile", help="AWS profile to use.")
@click.option("--region", help="AWS region to use.")
@click.option("--s3-bucket", help="AWS S3 bucket for artifacts.")
@click.option("--s3-prefix", help="AWS S3 prefix for artifacts.")
@click.pass_context
def cmd(
    ctx,
    *,
    profile: Optional[str],
    region: Optional[str],
    s3_bucket: Optional[str],
    s3_prefix: Optional[str],
):
    """Amazon Web Services commands."""
    logging.getLogger("botocore").setLevel(logging.ERROR)
    logging.getLogger("boto3").setLevel(logging.ERROR)
    sub = ctx.invoked_subcommand
    _config, _profile = config.CONFIG.get(), config.PROFILE.get()
    if _profile not in _config:
        if sub not in ("setup",):
            raise util.UserError(f"config profile {_profile!r} not found")
        _config.add_section(_profile)
    cfg = _config[_profile]
    ctx.default_map = dict()
    aws_util.init_session(
        profile or cfg.get("aws_profile", None), region or cfg.get("aws_region", None)
    )
    profile, region = aws_config.PROFILE.get(), aws_config.REGION.get()
    ctx.obj = dict(
        profile=profile,
        region=region,
        s3_bucket=s3_bucket or cfg.get("aws_s3_bucket", None),
        s3_prefix=s3_prefix or cfg.get("aws_s3_prefix", ""),
    )


cmd.add_command(cmd_setup.cmd)
cmd.add_command(cmd_id.cmd)
cmd.add_command(cmd_value.cmd)
cmd.add_command(cmd_tree.cmd)
cmd.add_command(cmd_dump.cmd)
cmd.add_command(cmd_update.cmd)
