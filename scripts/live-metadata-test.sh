#!/bin/sh
set -eu

config_path=${1:-tests/live/config.toml}

jayspray --config "$config_path" discover --limit 2
jayspray --config "$config_path" search SM-S928U1
jayspray --config "$config_path" discover --limit 2
