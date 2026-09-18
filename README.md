# MATCH · Heißer Draht

Eine eigenständige Spielstation für den mobilen Roboter: NFC-Tag scannen,
Spieler vom zentralen Dashboard begrüßen, den Spielablauf am UR beobachten und
das Ergebnis automatisch übertragen. Die lokale Webseite ist für das Roboterdisplay
ausgelegt und läuft standardmäßig auf **Port 8002**.

Die Station liest den UR ausschließlich über **Port 30011**. Sie startet und stoppt
keine Roboterprogramme und verändert keine Bewegungen oder Geschwindigkeiten.

## Start

Voraussetzungen: Python ab 3.10, [uv](https://docs.astral.sh/uv/), ein USB-Reader mit
der beiliegenden D1-mini/RC522-Firmware und Netzwerkzugriff auf UR und Dashboard.

```sh
git clone https://github.com/match-Misc/match-hot-wire.git
cd match-hot-wire
cp .env.example .env
cp .station.env.example .station.env
chmod 600 .env .station.env
# .env: NFC_STATION_KEY und den echten NFC_SERIAL_PORT eintragen.
# .station.env: Dashboard-Adresse und UR-Variablen prüfen.
./run-station.sh
```

`uv` installiert die festgeschriebenen Abhängigkeiten automatisch. Die Anzeige ist
unter `http://localhost:8002/` bzw. `http://<Roboter-PC>:8002/` erreichbar. Oben rechts
lässt sich Vollbild aktivieren. Ein anderer Port kann als Argument angegeben werden.

Der Reader braucht Linux-Portrechte (normalerweise Gruppe `dialout`). Nur **ein**
Prozess darf den seriellen Port öffnen. Ein vorher gestartetes Diagnose-Dashboard
oder ein Serial Monitor muss dafür beendet sein.

**Zuerst die Station, dann das UR-Programm starten.** Die Variablennamen werden vom
UR beim Programmstart übertragen. Nach einer neuen Verbindung zeigt die Station
an, wenn sie diesen Start noch benötigt.

## Ablauf und Konfiguration

- Ein registrierter Tag lädt den Spielernamen vom zentralen Server.
- Die Begrüßung bleibt bis zum Spielstart stehen.
- `Laeuft` wechselt auf `true`: Spieler und Override-Stufe werden festgehalten.
- `Laeuft` wechselt auf `false`: `Zeit` wird sofort eingefroren. Ein danach
  weiterlaufender UR-Zähler verändert das Ergebnis nicht.
- `Gewinner=true` bedeutet Sieg des Kindes, `false` Niederlage. Das Signal darf bei
  einer Niederlage unverändert `false` bleiben.
- Die Station sendet Zeit, Spieler, Schwierigkeit und Ausgang. Danach wartet sie
  wieder auf einen Tag. Netzwerk-Retries verwenden dieselbe Versuch-ID.

Der Override wird in zehn Stufen eingeteilt: **Drahtentdecker, Funkenfänger,
Kabelheld, Stromsurfer, Blitzjäger, Voltprofi, Hochspannungsheld, Turbofinger,
Blitzmeister, Drahtlegende**. Die Grenzen sind 0–<10 %, 10–<20 %, …, 90–100 %.
Diese Namen müssen auch am zentralen Dashboard konfiguriert sein.

Die vollständige Betriebsanleitung, Fehlerbehandlung und der bestätigte Hardwaretest
stehen in [STATION.md](STATION.md). Weitere Referenzen:

- [Dashboard-API](API.md)
- [Reader-Firmware und Verkabelung](NFC_READER_FIRMWARE.md)
- [systemd-Benutzerdienst](deploy/ndm-station.service)

Für den Dienst wird ein Checkout unter `~/NDM_ws/src/match-hot-wire` erwartet:

```sh
mkdir -p ~/.config/systemd/user
cp deploy/ndm-station.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ndm-station
```

Der Dienst startet mit der Benutzeranmeldung. Zugangsdaten, Datenbanken und Logs
gehören zur jeweiligen Installation und werden nicht ins Git-Repository übernommen.

## Tests

```sh
env -u PYTHONPATH uv run --locked pytest -q
```

Die Tests prüfen die UR-Paketdekodierung, NFC-Handshake und Wiederverbindung,
Spielerbindung, Override-Grenzen, Zeit-Erfassung, dauerhafte Ergebniswarteschlange
und vollständige Spielabläufe inklusive verlorener Serverantworten. Sie verwenden
lokale Testserver und serielle Pseudoterminals; keine echten Scores oder Bewegungen.

Die Spielstation entstand im Workspace des Projekts
[ndm-nfc](https://github.com/match-Misc/ndm-nfc). Dieses Repository enthält den
eigenständig lauffähigen Stationscode und die passende Reader-Firmware;
der zentrale Dashboardserver ist weiterhin ein separates System.
