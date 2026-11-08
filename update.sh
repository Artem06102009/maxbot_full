set -e

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
git fetch origin $BRANCH
git reset --hard origin/$BRANCH
git clean -fd -e .env -e data -e data/*

docker compose down --remove-orphans
docker compose build --no-cache
docker compose up -d

docker compose ps
docker compose logs --tail=25
