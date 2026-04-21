#!/usr/bin/env bash
# run_ingest.sh — manually trigger all scheduled ingestion jobs
set -euo pipefail

COMPOSE="sudo docker compose -f /home/daryn/parsnip/docker-compose.yml"

echo "=== arXiv ingestion ==="
$COMPOSE exec scheduler python -c "
import asyncio, sys
sys.path.insert(0, 'ingestion')
import ingest_arxiv
asyncio.run(ingest_arxiv.main_async(
    categories=['cs.AI','cs.LG','cs.CL','cs.CV','stat.ML','q-bio.GN','q-bio.NC','econ.GN'],
    max_per_cat=500,
    from_raw=None,
))
"

echo ""
echo "=== bioRxiv ingestion ==="
$COMPOSE exec scheduler python -c "
import asyncio, sys
sys.path.insert(0, 'ingestion')
import ingest_biorxiv
asyncio.run(ingest_biorxiv.main_async(
    server='biorxiv',
    days=7,
    categories=ingest_biorxiv.DEFAULT_CATEGORIES,
    limit=None,
    from_raw=None,
))
"

echo ""
echo "=== Joplin sync ==="
$COMPOSE exec scheduler python -c "
import asyncio, sys
sys.path.insert(0, 'ingestion')
import ingest_joplin
asyncio.run(ingest_joplin.main_async(full=False))
"

echo ""
echo "=== All ingestion jobs complete ==="
