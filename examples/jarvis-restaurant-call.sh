#!/usr/bin/env bash
#
# The call from the JARVIS demo, end to end, against a real brain.
#
#   ./examples/jarvis-restaurant-call.sh
#
# Runs in a throwaway brain so it can't touch yours. Nothing here is mocked —
# every step is a real event in a real hash-chained log.
#
set -euo pipefail

RSB="${RSB:-$(dirname "$0")/../rsb}"
export RAPP_SECOND_BRAIN_HOME="${RAPP_SECOND_BRAIN_HOME:-$(mktemp -d)}"

json() { "$RSB" --json "$@"; }
id_of() { python3 -c 'import json,sys;d=json.load(sys.stdin);print(d[sys.argv[1]]["id"])' "$1"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

printf '\nRAPP Second Brain — JARVIS restaurant call\nbrain: %s\n' "$RAPP_SECOND_BRAIN_HOME"

step "1. The owner's standing orders"
"$RSB" init --owner Kody >/dev/null
"$RSB" pref set preferred_dinner_time "19:00"
"$RSB" pref set party_default "2"
"$RSB" remember "Kody is allergic to shellfish" --tag health
"$RSB" contact add --name "Bella Vista" --phone "(555) 123-4567" --org Restaurant

step "2. The agent dials, with an objective and hard limits"
CALL=$(json call start \
  --to "Bella Vista" \
  --objective "Book a table for 2 on Friday at 7pm" \
  --constraint "party size exactly 2" \
  --constraint "no later than 20:00" \
  --provider retell | id_of call)
echo "   call $CALL"

step "3. The conversation"
"$RSB" call turn --call "$CALL" --role agent --text "Hi — I'd like to book a table for two this Friday at seven."
"$RSB" call turn --call "$CALL" --role peer  --text "Seven is fully booked I'm afraid. I could do seven forty-five?"
"$RSB" call turn --call "$CALL" --role agent --text "That's within my limits — let me confirm with him and call you straight back."
"$RSB" call turn --call "$CALL" --role peer  --text "No problem, I'll hold it for ten minutes."
"$RSB" call end --call "$CALL" --outcome counter_offer --summary "7pm unavailable; 7:45pm held for 10 minutes"

step "4. 7:45 is not 7:00 — so it PROPOSES, it does not book"
APPT=$(json appointment propose \
  --title "Dinner at Bella Vista (2)" \
  --with "Bella Vista" \
  --start "friday 19:45" \
  --call "$CALL" | id_of appointment)
echo "   proposal $APPT"
echo "   confirmed appointments right now: $(json appointment list --status confirmed | python3 -c 'import json,sys;print(json.load(sys.stdin)["count"])')"

step "5. It calls the owner back and asks"
APR=$(json approval request \
  --subject "Bella Vista offered 7:45pm instead of 7pm" \
  --detail "They'll hold it for ten minutes. Take it?" \
  --ref "$APPT" | id_of approval)

BACK=$(json call start --to "+15550000000" --objective "Call the owner for approval" | id_of call)
"$RSB" call turn --call "$BACK" --role agent --text "Bella Vista can't do seven, but they're holding seven forty-five. Want me to take it?"
"$RSB" call turn --call "$BACK" --role owner --text "Yeah, book it."
"$RSB" call end --call "$BACK" --outcome approved --success

step "6. The yes is recorded, and only now is it booked"
"$RSB" approval approve "$APR" --via phone
if "$RSB" approval check "$APR" >/dev/null; then
  "$RSB" appointment confirm "$APPT" --external-id "gcal_$(date +%s)"
else
  echo "   not approved — nothing booked"; exit 1
fi

step "7. What the owner sees"
"$RSB" brief

step "8. What can be proved"
"$RSB" verify
echo
echo "   The transcript survives the session:"
"$RSB" call show "$CALL" | sed 's/^/   /'

printf '\n\033[1mThe point:\033[0m the agent never booked outside the owner'"'"'s rules without asking,\n'
printf 'and every step of that is provable from %s/events.jsonl\n\n' "$RAPP_SECOND_BRAIN_HOME"
