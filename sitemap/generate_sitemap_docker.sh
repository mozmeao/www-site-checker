#!/bin/bash

set -exo pipefail

cd sitemap

docker build --pull \
             --platform "linux/amd64" \
             -t sitemap-generator \
             --build-arg "USER_ID=$(id -u):$(id -g)" \
             .

docker run --rm \
           --platform "linux/amd64" \
           --env-file .bedrock.env \
           -v "$PWD:/app/sitemap-data" \
           sitemap-generator ./run-generator.sh

if [[ -n "$SNITCH_URL" ]]; then
    curl "$SNITCH_URL"
fi
