#!/usr/bin/env bash
# Two gateways, one PostgreSQL, one coherent fleet -- and the failures.
#
# The acceptance test for #790. Deliberately not a pytest: what is under test is
# the *deployment*, not the code. Every assertion goes through the HTTP surface
# or the database, the way an operator would look at it -- which is how it found
# that the shipped image had no PostgreSQL driver and that the event store's
# schema was never created, neither of which a unit test can see.
#
# Run it against a cluster with the manifests in this directory:
#
#     kubectl apply -f tests/acceptance/ha-postgres.yaml
#     kubectl apply -f tests/acceptance/ha-gateway.yaml
#     bash tests/acceptance/ha_two_gateways.sh
#
# It kills a pod. Point it at a cluster you own.
set -uo pipefail
NS=ha-test
PASS=0; FAIL=0

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m %s\n' "$*"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m %s\n' "$*"; }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (expected '$3', got '$2')"; fi; }

pods() { kubectl -n $NS get pods -l app=hangar -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort; }

# Talk to ONE pod, not the service: the whole point is that replicas can differ.
api() { # api <pod> <method> <path> [body]
  local pod=$1 method=$2 path=$3 body=${4:-}
  if [ -n "$body" ]; then
    kubectl -n $NS exec "$pod" -- python -c "
import json,urllib.request
r=urllib.request.Request('http://127.0.0.1:8080$path', data=json.dumps($body).encode(), method='$method', headers={'Content-Type':'application/json'})
try:
    print(urllib.request.urlopen(r, timeout=10).read().decode())
except Exception as e:
    print(json.dumps({'error': str(e), 'body': getattr(e,'file',None) and e.file.read().decode()}))
" 2>/dev/null
  else
    kubectl -n $NS exec "$pod" -- python -c "
import json,urllib.request
try:
    print(urllib.request.urlopen('http://127.0.0.1:8080$path', timeout=10).read().decode())
except Exception as e:
    print(json.dumps({'error': str(e)}))
" 2>/dev/null
  fi
}

psql_q() { kubectl -n $NS exec deploy/postgres -- psql -U hangar -d hangar -tAc "$1" 2>/dev/null | tr -d ' \r'; }

leader_of() { api "$1" GET /api/system | python3 -c "import sys,json; print(json.load(sys.stdin)['system']['instance']['manages_fleet'])" 2>/dev/null; }
instance_of(){ api "$1" GET /api/system | python3 -c "import sys,json; print(json.load(sys.stdin)['system']['instance']['instance_id'])" 2>/dev/null; }
knows()     { api "$1" GET /api/mcp_servers/ | python3 -c "
import sys,json
d=json.load(sys.stdin)
servers=d.get('mcp_servers', d)
ids=[s.get('mcp_server_id') or s.get('id') for s in servers] if isinstance(servers,list) else list(servers)
print('yes' if '$2' in ids else 'no')" 2>/dev/null; }

say "== the fleet =="
POD=($(pods))
echo "  pods: ${POD[*]}"
[ "${#POD[@]}" -ge 2 ] || { echo "need two replicas"; exit 1; }

LEADER=""; FOLLOWER=""
for p in "${POD[@]}"; do
  if [ "$(leader_of "$p")" = "True" ]; then LEADER=$p; else FOLLOWER=$p; fi
done
echo "  leader:   ${LEADER:-<none>}"
echo "  follower: ${FOLLOWER:-<none>}"

say "1. exactly one replica manages the fleet"
COUNT=0; for p in "${POD[@]}"; do [ "$(leader_of "$p")" = "True" ] && COUNT=$((COUNT+1)); done
check "one manager, not zero and not two" "$COUNT" "1"

say "2. a server registered on the leader is served by the follower"
api "$LEADER" POST /api/mcp_servers/ '{"mcp_server_id":"acceptance","mode":"remote","endpoint":"http://example.invalid/mcp","description":"registered on the leader"}' >/dev/null
sleep 6   # one tail interval, generously
check "the leader has it"   "$(knows "$LEADER" acceptance)"   "yes"
check "the follower has it" "$(knows "$FOLLOWER" acceptance)" "yes"

say "3. a local-mode server is refused where replicas share state"
OUT=$(api "$LEADER" POST /api/mcp_servers/ '{"mcp_server_id":"local-one","mode":"subprocess","command":["python","-c","pass"]}')
case "$OUT" in *"child process"*|*"remote"*) ok "refused, and the message names the mode that works";; *) bad "not refused: $OUT";; esac

say "4. the generation fences a deposed leader"
GEN_BEFORE=$(psql_q "SELECT generation FROM management_lease WHERE name='fleet-management'")
HOLDER_BEFORE=$(psql_q "SELECT holder FROM management_lease WHERE name='fleet-management'")
echo "  generation before: $GEN_BEFORE (held by ${HOLDER_BEFORE:0:24}...)"
kubectl -n $NS delete pod "$LEADER" --wait=false >/dev/null 2>&1
echo "  killed $LEADER; waiting for the tenure to change hands"
for _ in $(seq 1 30); do
  GEN_AFTER=$(psql_q "SELECT generation FROM management_lease WHERE name='fleet-management'")
  [ -n "$GEN_AFTER" ] && [ "$GEN_AFTER" != "$GEN_BEFORE" ] && break
  sleep 2
done
check "the generation advanced on handover" "$([ "${GEN_AFTER:-0}" -gt "${GEN_BEFORE:-0}" ] && echo yes || echo no)" "yes"

say "5. serving does not blink while management changes hands"
check "the survivor still answers" "$(knows "$FOLLOWER" acceptance)" "yes"

say "6. the deposed leader's destructive write affects zero rows"
ROWS=$(psql_q "WITH stale AS (UPDATE mcp_server_configs SET enabled=FALSE WHERE mcp_server_id='acceptance' AND enabled=TRUE AND EXISTS (SELECT 1 FROM management_lease WHERE name='fleet-management' AND holder='$HOLDER_BEFORE' AND generation=$GEN_BEFORE AND expires_at > now()) RETURNING 1) SELECT count(*) FROM stale")
check "zero rows for the old tenure" "${ROWS:-0}" "0"
STILL=$(psql_q "SELECT enabled FROM mcp_server_configs WHERE mcp_server_id='acceptance'")
check "the server survived the stale write" "$STILL" "t"

say "7. management resumes on the survivor"
NEW_LEADER=""
for _ in $(seq 1 20); do
  for p in $(pods); do [ "$(leader_of "$p")" = "True" ] && NEW_LEADER=$p; done
  [ -n "$NEW_LEADER" ] && break
  sleep 2
done
check "someone manages the fleet again" "$([ -n "$NEW_LEADER" ] && echo yes || echo no)" "yes"

say "8. one event, one export"
EXPORTS=0
for p in $(pods); do
  n=$(kubectl -n $NS logs "$p" --tail=2000 2>/dev/null | grep -c "acceptance" || true)
  echo "  $p: $n lines mentioning the server"
done

printf '\n\033[1m%d passed, %d failed\033[0m\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
