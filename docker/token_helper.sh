#!/bin/sh
# docker/token_helper.sh
# Called by mbsync PassCmd to get a fresh OAuth2 access token.
# Usage: token_helper.sh <account_id>
ACCOUNT_ID="$1"
curl -sf "http://localhost:8000/api/internal/token/${ACCOUNT_ID}"
