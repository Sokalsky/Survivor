Chrome setup
1. Open the project's Draft page in Chrome.
2. Download /static/yahoo-draft-watcher.zip using Connect Yahoo. Extract it.
3. In chrome://extensions enable Developer mode, choose Load unpacked, and
   select the extracted folder containing manifest.json.
4. While on Survivor, open the extension and choose Pair Survivor tab. Grant
   access only to this selected origin. Ordinary production use requires HTTPS;
   localhost and 127.0.0.1 HTTP are permitted for local previews.
5. Switch to the Yahoo basketball draft room. Choose Watch Yahoo tab and grant
   access to that selected Yahoo origin.
6. Use field pickers to select the exact player name, current bid, leading team
   and explicit sold-status text. Pick the timer only if useful. Clicking while
   a picker is active selects a field and consumes the click; Escape cancels.
7. Prefer selecting completed-result rows and their player, winner and price
   cells as well. Select the row first, then cells. Optional bid-history rows
   need their own player name, bidder and amount; the watcher does not guess
   which player an older bid belongs to.
8. Map Yahoo team names to the franchise names on the Draft page. Player names
   use conservative exact normalization. Abbreviated or unfamiliar names require
   explicit mapping; Jalen/Jaylin Williams are never fuzzy matched.
9. Check the first nomination, bid changes and completed sale against Yahoo.
   Configure roster slots in Roster & reserve to match Yahoo, including BN and
   excluding nondraftable IL slots. The 15-player total defaults to league history;
   actual lineup constraints and Yahoo-specific eligibility must be confirmed.

Adapter status and limits
The current Yahoo draft DOM has NOT been inspected in this workspace. The
extension is a configurable passive DOM adapter, not a verified universal Yahoo
integration. No invented Yahoo selectors or undocumented WebSocket formats are
baked in. A real Yahoo draft/mock-draft calibration pass is still required.
The interface uses an auction-room layout; exact visual parity with the current
Yahoo room cannot be checked without seeing that room.

Mutation changes are observed immediately; a 180 ms poll covers missed mutations.
The initial bid waits for fields to settle. A sold-status signal must remain
stable for at least 350 ms. Result rows provide an independent confirmation path.
Only explicitly rendered text is read. Disappearance or a changed nomination is
never assumed to be a completed sale. A hidden, coalesced or never-rendered bid
cannot be recovered by this adapter. Start before the first nomination. Chrome
background throttling, virtualized lists and changed layouts can reduce coverage.
The page labels the log as observed bids and stops bid advice when stale.

Data and privacy
No Yahoo passwords, cookies, OAuth tokens, network response bodies or full page
DOM are read or sent. The extension has no Yahoo write operations. Host access is
optional and requested for each tab's exact selected origin. The receiver tab is
checked for the Survivor app marker and bound to a random nonce and session ID.
Updates travel via Chrome messaging and an isolated-world page bridge, not a
new public server endpoint. Existing Flask APIs and database requests remain
read-only. Other website visitors do not see this browser's draft activity.

Draft state is stored under survivor.draft.v1 in this browser's localStorage.
The first session freezes a compact snapshot of its saved forecast and confirmed
keepers, identified by the original run ID. Subsequent deployments do not rewrite
that snapshot. New live session explicitly starts from the latest saved forecast.
Browser data deletion loses local sessions; Export session creates a JSON backup.
Import validates and replays that snapshot and events before it can be restored.
Exports include one draft session's frozen inputs and accepted events; pending
mapping/ignored updates and extension selectors remain in browser storage.
Practice uses real projections and simulated activity in an independent session.

The extension persists up to 2,000 undelivered updates and retains stable event IDs
across worker restarts. It deletes queue entries only after the page durably saves
and acknowledges them. A full queue pauses capture rather than silently dropping
the oldest events. Reconnection can replay updates; deduplication prevents double
spending. A new session cannot silently receive the previous session's queued
updates. Explicit queue clearing discards those unsent updates. Review gaps using
Yahoo's completed results or manual entry. Multiple tabs cannot silently overwrite
a changed draft: a storage conflict requires reloading before another edit.
