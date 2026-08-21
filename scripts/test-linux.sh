#!/bin/sh
set -eu

docker build --file packaging/docker/Dockerfile.test --tag jayspray-test .
docker run --rm jayspray-test
