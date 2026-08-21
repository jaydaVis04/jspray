#!/bin/sh
set -eu

docker build --file packaging/docker/Dockerfile.test --tag samsung-fw-sync-test .
docker run --rm samsung-fw-sync-test
