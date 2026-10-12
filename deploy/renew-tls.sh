#!/bin/sh
set -eu
docker run --rm \
  -v /opt/rdrs/deploy/tls:/etc/letsencrypt \
  -v /opt/rdrs/deploy/acme:/var/www/acme \
  certbot/certbot@sha256:f70ad0adbb7e117f0fe42a63c553f28ea451edabc0148757b6efcd9735acaa20 \
  renew --non-interactive --quiet
docker exec rdrs-public-1 nginx -t
docker exec rdrs-public-1 nginx -s reload
