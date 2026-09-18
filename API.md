# Station API

Game stations call the dashboard over HTTP on the event LAN. The reader's wiring, firmware, and USB serial messages are documented in [NFC_READER_FIRMWARE.md](NFC_READER_FIRMWARE.md). Use `http://<server-LAN-address>:8001` as the base URL, or substitute the port used by the server. Send the shared `NFC_STATION_KEY` in the `X-API-Key` header on **every** request. Keep that key out of browser code and logs. The API returns JSON; request bodies for `POST` must use `Content-Type: application/json`.

The usual sequence is: fetch games and their current difficulty IDs, read a tag UID, look up the player, run the game, then submit the result. Retain the returned numeric `player_id` for that attempt. A tag can later be reassigned; delayed results still belong to the player who started the game.

## Discover games

`GET /api/games` returns the configured game IDs and difficulty IDs. The admin can change difficulty lists, so stations should use the returned values rather than assume the defaults.

```sh
curl -H "X-API-Key: $NFC_STATION_KEY" http://localhost:8001/api/games
```

```json
{"games":[{"id":"puzzle","difficulties":["leicht","mittel","schwer"]},{"id":"vier_gewinnt","difficulties":["leicht","mittel","schwer"]},{"id":"heisser_draht","difficulties":["leicht","mittel","schwer"]}]}
```

## Look up a tag

`GET /api/tags/{uid}` returns the assigned player. Pass the hexadecimal UID read by your NFC reader; case is ignored, and `:` or `-` separators are accepted. The UID must represent 4–10 bytes. Unknown and malformed UIDs both return `404`.

```sh
curl -H "X-API-Key: $NFC_STATION_KEY" http://localhost:8001/api/tags/04A1B2C3
```

```json
{"id":42,"name":"Alex"}
```

Use `id` as the player identifier. Names are for display and may repeat.

## Submit a result

`POST /api/results` records one completed attempt. All six fields are required; extra fields are rejected.

| Field | Value |
| --- | --- |
| `submission_id` | A UUID created once for this attempt and reused on every retry. |
| `player_id` | Positive integer `id` returned by tag lookup at game start. |
| `game` | A game `id` from `/api/games`. |
| `difficulty` | A configured difficulty ID for that game; send as a string. |
| `duration_ms` | Positive integer milliseconds (up to signed 64-bit maximum). |
| `outcome` | `win`, `loss`, or `draw` (Unentschieden), from the player's perspective. |

Use `draw` for tied games such as Vier gewinnt. Draws count as completed attempts, but only wins appear in the leaderboards.

```sh
curl -X POST http://localhost:8001/api/results \
  -H "X-API-Key: $NFC_STATION_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"submission_id":"509a64d8-0fd8-4e48-b16c-44386553cbe2","player_id":42,"game":"puzzle","difficulty":"schwer","duration_ms":42500,"outcome":"win"}'
```

```json
{"id":17,"recorded_at":"2026-09-17T12:34:56.123456+00:00","removed":false,"created":true}
```

`recorded_at` is set by the server in UTC. A retry with the **same UUID and data** returns the same result with `created:false`; keep the UUID and original payload until you receive a response. Reusing a UUID with different data returns `409`. If an admin later removes a result, an exact retry returns that result with `removed:true` and `created:false`; it does not recreate it. Results are accepted with HTTP `200` for both new submissions and exact retries.

## Errors and retries

| Status | Meaning | Station action |
| --- | --- | --- |
| `401` | Missing or wrong `X-API-Key`. | Fix the configured key. |
| `404` | Tag/player not found (depending on endpoint). | Register the tag or recheck the player ID. |
| `409` | Submission UUID already used with different data. | Investigate the duplicate; do not change the stored attempt's payload. |
| `422` | Invalid request body or result value. | Correct the fields or refresh `/api/games`. |

On a timeout or transport failure, retry the exact `POST /api/results` payload. Do not generate a new `submission_id` for the retry. A new game attempt always gets a new UUID. FastAPI's interactive schema is also available at `/docs` on the server.
