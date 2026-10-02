"""Names vulture cannot see being used, each with the reason it exists. CI runs:

    vulture src/knos scripts --min-confidence 60

Everything else vulture would report is deleted, not listed here.
"""
# ruff: noqa
from knos import cli
from knos.settle import pay as settle_pay

_ = object()

# Typer registers these with decorators and calls them when a person types the command.
cli._main, cli.bounty, cli.due, cli.mainnet_check_cmd
_.judge_cmd, _.checks_hash, _.observe, _.lint, _.learn, _.gate, _.check, _.run

# The Sibyl store keeps its connection open for its lifetime.
_._storage

# knos.settle: the public client of knos-oidc and knos-pay. Constants mirror the programs' (clients and tests read
# them), and the dataclass fields are the accounts' layouts.
settle_pay.FAUCET_CAP, settle_pay.MAX_AMOUNT, settle_pay.MIN_AMOUNT, settle_pay.ERRORS
_.not_before, _.vetoes, _.done, _.exp, _.review, _.token_funded, _.funder_id, _.checks, _.wf_repo_hash, _.wf_sha
