#!/bin/bash

set -exo pipefail

cd sitemap

docker build --pull \
             -t sitemap-generator \
             --build-arg "USER_ID=$(id -u):$(id -g)" \
             .

docker run --rm \
           --env-file .bedrock.env \
           -v "$PWD:/app/sitemap-data" \
           sitemap-generator ./run-generator.sh
