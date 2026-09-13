#!/bin/bash
# Watches RunPod pods cb36f9w0pji1uv (runpod-1) and gjmr67qeqsghyr (runpod-2)
# until both self-terminate, then verifies each one's independent shard
# actually landed on origin/dino-sharding BEFORE declaring it safe -- a pod
# disappearing is not proof its data was saved, only the git tree is.
REPO="/Users/jerryhu/Documents/Documents/Hackathons/54 Hackathon 2026/fleetworth"
cd "$REPO" || exit 1

declare -A PODS=( [cb36f9w0pji1uv]=runpod-1 [gjmr67qeqsghyr]=runpod-2 )
declare -A DONE

while [ "${#DONE[@]}" -lt "${#PODS[@]}" ]; do
  sleep 120
  ALIVE_IDS=$(runpodctl pod list -o json 2>/dev/null | grep -o '"id": "[a-z0-9]*"' | sed 's/"id": "//;s/"//')
  for pod_id in "${!PODS[@]}"; do
    name="${PODS[$pod_id]}"
    [ -n "${DONE[$pod_id]:-}" ] && continue
    if ! echo "$ALIVE_IDS" | grep -q "$pod_id"; then
      git fetch origin -q
      if git ls-tree -r --name-only origin/dino-sharding -- "data/independent_shards/" 2>/dev/null \
          | grep -q "^data/independent_shards/${name}\.npy$"; then
        echo "[$(date -u +%H:%M:%S)] $name ($pod_id) terminated -- shard CONFIRMED saved on origin. OK."
      else
        echo "[$(date -u +%H:%M:%S)] $name ($pod_id) terminated -- ⚠ shard NOT found on origin. POSSIBLE DATA LOSS, investigate."
      fi
      DONE[$pod_id]=1
    fi
  done
done
echo "[$(date -u +%H:%M:%S)] Both pods accounted for. Watcher exiting."
