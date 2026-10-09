Chrome setup — watcher 0.3.3
1. Download /static/yahoo-draft-watcher.zip, extract it, and load the folder in
   chrome://extensions using Load unpacked. For an existing install, replace the
   contents of its folder and click Reload. Refresh both draft tabs after updating.
2. The watcher requires access only to basketball.fantasysports.yahoo.com and the
   Survivor production origin for automatic discovery. Optional manual pairing
   supports another HTTPS Survivor origin or localhost. Allow in Incognito when
   applicable; automatic pairing stays within the same regular/incognito context.
3. Open Survivor /?yahooMock=1#draft and join a Yahoo salary-cap mock before it
   starts. The room connects automatically: no pairing, team entry, budget entry,
   roster-size entry, or field picking in the normal mock workflow.
4. The adapter reads the rendered Budget roster list, Max Offer/Budget auction
   panel, current price/leader, and explicit completed-pick regions. It never
   treats the offer button as the current bid or infers a sale from a timer ending.
5. Check the first real Yahoo nomination/bid/result when testing a new layout.
   Advanced setup retains explicit pairing, CSS overrides, and field pickers as
   recovery tools. The owner confirmed bids, an own purchase and the next
   nomination in a live mock with 0.3.2; automated checks still use illustrative DOM.

Automatic import is mock-only. Each new Yahoo room creates a fresh test, backs up
the preceding mock locally, and keeps undelivered old-room events out of the new
room. It does not change real drafts, keeper evidence, forecasts, or manager
history. Reopening the same room preserves its session. Concurrent rooms cannot
steal the active capture. Multiple destinations require an explicit selection.

Join before the first sale. If starting budgets cannot be reconstructed from
rendered completed picks, the adapter waits instead of inventing missing data.
Roster size is imported; position-specific slot eligibility remains unconfirmed
unless explicitly configured. Unique player initials/diacritics are resolved
against the saved projection names; ambiguous initials are not guessed.

For a real league draft, use Advanced setup to explicitly pair the real Survivor
workspace and Yahoo draft tab, then enable Save real draft. An identified mock or
unknown room never automatically chooses a real workspace.

Watcher 0.3.3 — live clock updates (October 9, 2026)
The owner confirmed 0.3.2 working in Yahoo: Jokic was purchased, both budgets
showed $116, and the SGA nomination and $72 bid matched. The remaining clock issue
was in the website: timer messages arrived but the clock only rerendered with a
draft event. The website now updates the clock on each heartbeat, and the watcher
sends rendered clock changes immediately instead of waiting for the 1.5s heartbeat.
The browser flow verifies 00:06, a bid resetting it to 00:10, and then 00:09 with
no extra bid event. No local clock, synthetic bid or timer-based sale is introduced.
This clock fix needs both the updated site and extension; refresh after deployment.

Watcher 0.3.2 — own bids and price-less last picks (October 9, 2026)
The live mock imported its room and other managers' bids, then stalled when
the owner bid $75 on Jokic: the roster row said You, the auction card said max,
and the completed Last pick strip had no price. A unique matching team-row bid
badge now identifies the bidder. A price-less Last pick requires a captured
winning bid, that exact cash decrease, and one added roster spot before a sale
is emitted. Incomplete or ambiguous evidence remains unresolved.
The reader retains confirmed results across polling and same-version reinjection.
Browser checks reproduce the $75 own bid, $125 remaining, 12 open slots, and
Wembanyama's next nomination through the extension/app transport. These checks
use illustrative DOM, not an actual Yahoo capture. Live retest remains required.
Reload the unpacked extension, refresh both tabs, and join a fresh mock before
the first nomination; old missed observations are not reconstructed by guessing.

Adapter status and limits
The current Yahoo draft DOM has NOT been inspected in this workspace. The
extension has automatic semantic DOM reading plus optional selector overrides;
it is not a verified universal Yahoo integration. No invented Yahoo selectors or undocumented WebSocket formats are
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
No Yahoo passwords, cookies, OAuth tokens or network response bodies are read or
sent. Page elements are examined locally; only normalized draft information is
relayed, never full-page HTML. The extension has no Yahoo write operations.
Automatic host access is limited
to Yahoo Fantasy Basketball and the Survivor production origin; other destinations
use optional access to the selected origin. The receiver tab is
checked for the Survivor app marker and bound to a random nonce and session ID.
Updates travel via Chrome messaging and an isolated-world page bridge. Real drafts
can be saved through Survivor's key-protected recording API. Saved bids, rosters,
projections and manager summaries are visible to everyone using the app. The
extension does not receive the recording key. Forecast inputs stay read-only.

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

Legacy explicit pairing: pairing Survivor pauses capture. Explicitly choose Watch Yahoo
tab after pairing, including when switching between real and mock workspaces.
For Yahoo mock testing use the Open Yahoo mock test link on Survivor's Draft page
(/?yahooMock=1#draft), configure the test teams and budget, then pair that page.
Mock activity is stored separately and cannot enter real draft history.
For the actual league draft use the regular page and enable Save real draft.


Watcher 0.3.1 ? nomination capture correction (October 9, 2026)
The owner's actual Yahoo mock successfully imported 12 teams, $200 budgets and
13 roster slots, but did not display the first nomination. The supplied screenshot
shows Giannis at $6, Scott leading, projected price $55, and bid badges in team rows.

Fixed reproduced parser failures:
- Expand from the bidding-controls container to the enclosing nomination card.
- Distinguish the actual current bid from the offer input, budget and projected price.
- Preserve the team name and wallet when Yahoo adds a last-bid dollar badge.
- Read visible player text even if its title attribute describes a UI action.
- Keep the reader's completed-pick region across periodic script reinjection.

The popup distinguishes waiting from an explicit user pause. Advanced setup adds
Download diagnostics: local normalized observations, matched field candidates and
connection state, without the recording key, cookies or full-page HTML.

Validation: 87 JavaScript cases pass. The full local browser flow runs the actual
background, reader, capture, bridge and app scripts with emulated Chrome messaging
and an illustrative Yahoo fixture. It verifies automatic import, Giannis $6/Scott,
a bid ladder, an explicit sale, the $159 winning budget, the next nomination, and
zero real-recording writes. This fixture is not the actual Yahoo DOM; corrected
live nomination/bid/sale capture remains to be verified by the owner.
