#!/usr/bin/env bash
# Runs ON the EC2 instance, started by the GitHub Actions deploy job through AWS SSM.
# Usage: remote-deploy.sh <full image uri, e.g. 123456789012.dkr.ecr.ap-south-1.amazonaws.com/infobot:<sha>>
#
# Pulls the image, restarts the stack, waits for the bot's own health check and, if the
# new version does not become healthy, puts the previous image back and exits non-zero
# (which fails the workflow run).
set -euo pipefail

APP_DIR=/opt/infobot
NEW_IMAGE="${1:?usage: remote-deploy.sh <image uri>}"
cd "$APP_DIR"

if [ ! -s app.env ]; then
  echo "ERROR: $APP_DIR/app.env is missing or empty."
  echo "Create it once with the bot's secrets (see deploy/README.md, step 4), then re-run the deploy."
  exit 2
fi
if [ ! -s .env ]; then
  echo "ERROR: $APP_DIR/.env is missing. The instance bootstrap should have written it."
  exit 2
fi

registry="${NEW_IMAGE%%/*}"               # 123456789012.dkr.ecr.ap-south-1.amazonaws.com
region="$(echo "$registry" | cut -d. -f4)"
aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "$registry"

previous_image="$(grep -E '^IMAGE=' .env | head -n1 | cut -d= -f2- || true)"

set_image() {
  if grep -q '^IMAGE=' .env; then
    sed -i "s|^IMAGE=.*|IMAGE=$1|" .env
  else
    echo "IMAGE=$1" >> .env
  fi
}

wait_healthy() {
  local status="missing"
  for _ in $(seq 1 60); do
    status="$(docker inspect -f '{{.State.Health.Status}}' infobot-app 2>/dev/null || echo missing)"
    [ "$status" = "healthy" ] && return 0
    sleep 2
  done
  echo "infobot-app health after 120 s: $status"
  return 1
}

echo "Deploying $NEW_IMAGE (previous: ${previous_image:-none})"
set_image "$NEW_IMAGE"
docker compose pull app
docker compose up -d --remove-orphans

if wait_healthy; then
  echo "Healthy. Containers:"
  docker compose ps
  # Keep disk use bounded: drop images older than 3 days that no container uses.
  docker image prune -af --filter "until=72h" >/dev/null || true
  exit 0
fi

echo "---- last log lines from the new version ----"
docker logs --tail 60 infobot-app 2>&1 || true
if [ -n "$previous_image" ]; then
  echo "Rolling back to $previous_image"
  set_image "$previous_image"
  docker compose up -d --remove-orphans
  wait_healthy && echo "Rollback is healthy." || echo "WARNING: rollback is not healthy either, check the instance."
else
  echo "No previous image to roll back to (first deploy)."
fi
exit 1
