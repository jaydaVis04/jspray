#!/bin/sh
set -eu

config_path=${1:-tests/fixtures/live-config.toml}

fwtool --config "$config_path" discover --limit 2
fwtool --config "$config_path" search SM-S928U1
fwtool --config "$config_path" discover --limit 2
