# NFC reader firmware and game station integration

This is the implementation contract for the current [D1 mini/RC522 sketch](firmware/nfc_reader/nfc_reader.ino). It is intended for agents building the Puzzle, Vier gewinnt, and Heißer Draht game stations. The firmware reports NFC tag UIDs over USB serial. A process on the **game station computer** looks up the player and sends results to the dashboard using [API.md](API.md); the ESP8266 does not make HTTP requests.

The dashboard has its own reader for admin registration and tag diagnosis. A game station needs its **own locally connected reader**, or another reader implementation that produces the same UID. Do not have two processes open one serial port.

## Hardware and flashing

Use an ESP8266 D1 mini and an MFRC522-compatible 13.56 MHz reader. The sketch uses the ESP8266 Arduino board support and the Arduino `MFRC522` library.

| RC522 pin | D1 mini pin |
| --- | --- |
| 3.3 V | 3V3 |
| GND | GND |
| SCK | D5 |
| MISO | D6 |
| MOSI | D7 |
| SDA / SS | D8 |
| RST | D3 |

Power the RC522 from **3.3 V**. Open `firmware/nfc_reader/nfc_reader.ino` in the Arduino IDE, select the D1 mini board and its USB port, install the ESP8266 board support and `MFRC522` library, then upload. The sketch configures SPI at 1 MHz and the reader's average receive gain (33 dB). Keep these settings when copying the firmware: maximum gain caused receive errors with the connected NTAG215.

The USB serial port runs at **115200 baud, 8N1**. On Linux, prefer a stable `/dev/serial/by-id/...` path; on Windows, use the board's COM port. For the dashboard's admin reader, set `NFC_SERIAL_PORT` to that path. A game station should configure its own serial path in its own process; `NFC_SERIAL_PORT` configures only this dashboard application.

## Serial protocol

The stream is UTF-8 **JSON Lines**. Send each command as one JSON object followed by `\n`; the firmware ignores `\r`. Firmware events use Arduino `Serial.println`, so they end in `\r\n`. Send compact, single-line commands with ordinary string fields. Its input parser is deliberately small, not a general JSON parser, and drops input lines longer than 450 characters. Ignore non-JSON startup bytes, malformed lines, and event types your station does not use. After a disconnect, reopen the port and request `hello` again.

On boot and whenever the host sends `{"command":"hello"}`, the reader sends a message such as:

```json
{"type":"hello","reader":"MFRC522","version":146}
```

`version` is the decimal value of the RC522 `VersionReg` register (146 is 0x92). The dashboard considers `0x82`, `0x88`, `0x90`, `0x91`, and `0x92` verified. A reply with another version, especially `0`, means the USB board responded but the RC522 was not verified. The dashboard retries `hello` every three seconds until verification.

### Scan events

Presenting a tag emits:

```json
{"type":"scan","uid":"04A1B2C3"}
```

The UID is uppercase hexadecimal with no separators. The HTTP API accepts a 4–10 byte UID and also tolerates lowercase, `:`, or `-` separators. Keep the canonical uppercase form in the game station. The UID identifies the player; the optional NDEF URL stored on a tag is **not** used for game lookup.

The firmware emits one scan for a newly presented UID. Keeping a tag on the reader does not produce a continuous stream of scans. Removing it for at least **600 ms** lets the same tag produce a new scan; changing to another UID also counts as a new presentation. The scan loop has a 50 ms delay. Game software should still handle duplicate events and only accept a scan when its start screen is ready.

For registration, the dashboard sends an `arm` command with a 32-character hexadecimal token. The next eligible scan includes that token and consumes it:

```json
{"command":"arm","token":"0123456789abcdef0123456789abcdef"}
{"type":"scan","uid":"04A1B2C3","arm":"0123456789abcdef0123456789abcdef"}
```

`arm` requires a tag already on the reader to be removed before it can fire. `{"command":"cancel"}` clears the armed token. **Game stations do not need `arm`**: ordinary `scan` events are emitted without it. The dashboard accepts a registration only when the echoed `arm` token matches its pending request; a normal scan never creates a player. The dashboard's registration timeout is 30 seconds.

### Other events

About once per second, the firmware emits `rf_status` with these integer fields:

| Field | Meaning |
| --- | --- |
| `wake_ok`, `wake_timeout`, `wake_error` | Successful, timed out, and failed wake/request attempts since boot. |
| `select_ok`, `select_error` | Successful and failed UID selections since boot. |
| `last_wake_error`, `last_select_error` | Last library status code; 255 before an error is recorded. |
| `last_wake_length`, `last_reader_error`, `last_collision` | Length of the last bad wake response and the RC522 error/collision registers. |

These are diagnostic counters, not player events. They reset when the firmware restarts. A game station can ignore them after logging reader health.

## Admin-only tag inspection and URL writing

These commands are used by the dashboard's **Admin → Tag-Diagnose** page. They are not needed to start games or submit results.

| Host command | Firmware response |
| --- | --- |
| `{"command":"inspect","token":"<32 hex chars>"}` | `type:"inspect"` with the same token, UID, decimal SAK, ATQA hex, optional `tag_version` and `cc` hex, up to 128 bytes of `user_hex`, and optional `read_error`. |
| `{"command":"write_ndef","token":"<32 hex chars>","uid":"<UID>","data_hex":"<NDEF TLV hex>"}` | `type:"write_ndef"` with the same token, UID, and `verified:true` after readback. |
| `{"command":"cancel_tag","token":"<32 hex chars>"}` | Cancels the matching pending tag job; there is no success event. |

On failure, the reader emits `{"type":"tag_error","token":"...","code":"..."}`. Current codes are `invalid_command`, `wrong_tag`, `unsupported`, `write_failed`, and `verify_failed`. Inspection read failures appear in `read_error` (`read_failed` or `not_type2`) inside an `inspect` response. The dashboard times out a pending tag job after 120 seconds.

The dashboard builds the NDEF URI payload in [ndm-nfc's NDEF module](https://github.com/match-Misc/ndm-nfc/blob/main/nfc_dashboard/ndef.py). A write is limited to a writable NFC Type 2 tag, the UID from the preceding inspection, the tag's advertised capacity, and at most 128 user-memory bytes. The firmware writes from page 4, commits the first page last, then reads the written bytes back. It does not change UID, lock, or configuration pages. Registration itself **only assigns the UID**; writing the shared HTTP(S) URL is a separate admin action. The current URL limit is 120 UTF-8 bytes before NDEF framing and padding.

## Game station flow

1. Open the station's local reader at 115200 baud. Send `{"command":"hello"}\n`; wait for an `MFRC522` identity with a verified version. Retry the request about every three seconds until verified, since opening the USB port can reset the D1 mini. Reconnect and repeat this step if USB disappears.
2. While the game is ready for a player, consume a `scan` event and take its `uid`. Ignore `rf_status`, admin command responses, and scans that arrive during an active attempt.
3. Call `GET /api/tags/{uid}` on the dashboard, sending `X-API-Key: <NFC_STATION_KEY>`. An unknown or unregistered UID returns 404; show a registration prompt instead of starting a scored attempt.
4. Store the returned numeric `id` as `player_id` for **this attempt**, fetch valid game and difficulty IDs from `GET /api/games`, and run the game.
5. Submit `POST /api/results` with a new attempt UUID, that captured `player_id`, the game ID, configured difficulty, positive duration in milliseconds, and `win`, `loss`, or `draw` (Unentschieden). Retry the **same UUID and payload** after transport failures. See [API.md](API.md) for exact requests and responses.

Keep `NFC_STATION_KEY` in the game host configuration, not in the ESP8266 firmware or browser JavaScript. The dashboard defaults to port 8001 on the event LAN. The player ID captured at game start stays attached to that attempt even if an admin later reassigns the tag.

## Quick acceptance check

1. With the firmware flashed, open the serial port at 115200 and send `{"command":"hello"}\n`. Check for a verified `hello` response. An ESP8266 boot message may appear before it; discard non-JSON text.
2. Present a tag. Check for one `scan` with an uppercase UID. Keep the tag in place and confirm scans do not repeat. Remove it for at least 600 ms and present it again to get another scan.
3. Register the UID in the dashboard's Admin page and confirm `GET /api/tags/{uid}` returns the expected player. Test an unknown UID separately.
4. Complete one attempt through the station and confirm the result appears on the dashboard. Retry that same submission and confirm it is not duplicated.
5. For optional URL writing, use Admin → Tag-Diagnose to inspect, write, and verify on the **actual** tag, then check the URL with a phone. UID scanning has been observed on hardware; physical NDEF writing and phone reading still need confirmation on the event's tags.

If no UID appears, check 3.3 V power, the SPI pin mapping, short wiring, antenna placement, and the `rf_status` wake/select counters. A `hello` response alone proves only the USB serial path; the version and successful UID selection prove more of the reader path.
