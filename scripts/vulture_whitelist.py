"""Names vulture cannot see being used, each with the reason it exists. CI runs:

    vulture src/knos scripts/vulture_whitelist.py --min-confidence 60

Everything else vulture would report is deleted, not listed here.
"""
# ruff: noqa
from knos import cli, sdk
from knos.jobs import cli as jobs_cli, worker
from knos.pro import cli as pro_cli
from knos.team import cli as team_cli

_ = object()

# Typer registers these with decorators and calls them when a person types the command.
cli._no_command, cli.guard_cmd, cli.private_cmd, cli.mcp_cmd, cli.help_cmd
jobs_cli.list_, jobs_cli.sibyl_cmd, jobs_cli.relay_cmd, jobs_cli.serve_cmd, jobs_cli.stats_cmd
pro_cli.spend, pro_cli.budget_show, pro_cli.budget_set, pro_cli.report_cmd, pro_cli.budget_chain_show
pro_cli.budget_revoke, pro_cli.budget_raise, pro_cli.budget_clear, pro_cli.budget_fund, pro_cli.budget_agents
pro_cli.budget_sweep, pro_cli.pay_cmd, pro_cli.pro_status, pro_cli.pro_buy, pro_cli.pro_check, pro_cli.pro_activate
team_cli.agent_record, team_cli.prove, team_cli.mirror

# http.server.BaseHTTPRequestHandler calls these by name (board, Actions server, relay).
_.do_GET, _.do_POST, _.do_PUT, _.do_OPTIONS, _.log_message

# Library attributes set for their side effects: sqlite3 rows by name, shlex splitting, the Windows console API.
_.row_factory, _.whitespace_split, _.dwSize

# The public Python SDK (docs/INTEGRATE.md, examples/langgraph_team.py) and what Worker.once() returns to callers.
sdk.Knos, sdk.Knos.holder_of, sdk.Knos.memory_client, worker.Outcome.delivered
