# Heißer Draht: NFC-Spielstation auf mur620c

Die Station verbindet den USB-NFC-Reader mit dem UR `ur10_r` und dem zentralen
Dashboard auf `http://10.145.8.144:8001`. Ihre Anzeige läuft unter
**http://mur620c:8002/** (auf dem Roboter-PC selbst: `http://localhost:8002/`).
Der Knopf **Vollbild** vergrößert die Anzeige auf dem Roboterdisplay.

## Spielablauf

1. Tag auflegen. Die Station fragt `GET /api/tags/{uid}` ab und begrüßt den Spieler.
2. Der Spieler drückt den physischen Startknopf am Roboter.
3. Der Wechsel von `Laeuft=false` nach `Laeuft=true` startet die Auswertung. Spieler-ID,
   Geschwindigkeits-Override und Schwierigkeit werden für diesen Versuch festgehalten.
4. Beim Wechsel auf `Laeuft=false` wird `Zeit` (Sekunden) sofort eingefroren. Der
   Zähler darf im UR danach weiterlaufen. `Gewinner=true` heißt **Kind gewonnen**,
   `false` heißt **Kind verloren**. Das Gewinner-Signal wird eine halbe Sekunde
   entprellt, dann sendet die Station `POST /api/results` mit der eingefrorenen Zeit.
   Für eine Niederlage darf `Gewinner` während der gesamten Runde `false` bleiben;
   eine Änderung dieses Signals ist keine Voraussetzung für die Übertragung.
5. Die Anzeige zeigt das Ergebnis sechs Sekunden und wartet anschließend wieder
   auf einen Tag. Für eine weitere Runde denselben Tag kurz abheben und neu auflegen.

Ein weiterer Tag während einer Anmeldung oder eines Spiels ersetzt den Spieler
nicht. Die Anmeldung bleibt bis zum Spielstart bestehen. Unbekannte Tags
werden nicht registriert; dafür weiterhin das zentrale Dashboard verwenden.

## UR-Anbindung

Die Verbindung verwendet ausschließlich die **Primary Read-Only-Schnittstelle auf
Port 30011**. Die Station sendet keine URScript-, Bewegungs-, Start-, Stopp- oder
Override-Befehle. Grundlage ist die
[offizielle UR-Protokollbeschreibung](https://docs.universal-robots.com/tutorials/communication-protocol-tutorials/primary-secondary-guide.html).

Auf dem tatsächlichen Programm `NDW26_Draht.urp` wurden diese Variablennamen beobachtet:

| Variable | Bedeutung |
| --- | --- |
| `Laeuft` | bool: laufendes Spiel |
| `Zeit` | Zahl: Spieldauer in Sekunden |
| `Gewinner` | bool: Kind hat gewonnen |

Die Werte müssen global sichtbar sein. Das UR-Programm soll vor dem nächsten
Spiel `Laeuft=false` melden und am Ende den Gewinner setzen, danach `Laeuft=false`.
Der Zeitwert in diesem ersten End-Snapshot wird gespeichert; ein weiterlaufender
Zeit-Zähler verändert das Ergebnis nicht. Den Endzustand mindestens einen Übertragungszyklus
(empfohlen 0,2 Sekunden) sichtbar lassen. Ein lediglich zwischen zwei
Übertragungszyklen aufblitzender Wert kann nicht von außen erkannt werden.

**Zuerst die Station starten, dann das UR-Programm am Bedienpanel starten.** Der UR
sendet die Namensliste nur beim Programmstart. Nach einer neuen TCP-Verbindung
wartet die Station erneut auf diese Liste und zeigt das an; sie errät keine
Variablenindizes. Groß-/Kleinschreibung und Leerzeichen/Unterstriche werden nur bei
eindeutiger Zuordnung toleriert. Ein bereits laufendes Spiel wird nicht nachträglich
einem neuen Spieler zugeordnet. Bei Verbindungsverlust während eines Spiels wird
kein Ergebnis erfunden; der Versuch bleibt als unterbrochen in der lokalen Datenbank.

## Zehn Schwierigkeitsstufen

Verwendet wird `targetSpeedFraction` aus den UR-RobotMode-Daten: der eingestellte
Override, nicht die effektive, eventuell sicherheitsbedingt begrenzte Geschwindigkeit
(`speedScaling`). Der Wert wird beim Spielstart eingefroren; Änderungen während
einer Runde ändern deren gespeicherte Stufe nicht. Die Station stellt den Regler
selbst niemals um.

| Override | Stufe / an die API gesendeter Name |
| --- | --- |
| 0 bis unter 10 % | Drahtentdecker |
| 10 bis unter 20 % | Funkenfänger |
| 20 bis unter 30 % | Kabelheld |
| 30 bis unter 40 % | Stromsurfer |
| 40 bis unter 50 % | Blitzjäger |
| 50 bis unter 60 % | Voltprofi |
| 60 bis unter 70 % | Hochspannungsheld |
| 70 bis unter 80 % | Turbofinger |
| 80 bis unter 90 % | Blitzmeister |
| 90 bis einschließlich 100 % | Drahtlegende |

Diese zehn Namen müssen im zentralen Dashboard unter **Admin → Konfiguration →
Heißer Draht** genau in dieser Reihenfolge stehen. Sie wurden am 18.09.2026 über
`GET /api/games` auf dem zentralen Server bestätigt. Die Station verändert die
Serverkonfiguration im normalen Betrieb nicht.

## Installation und Betrieb

Wie beim Dashboard werden die Abhängigkeiten über `uv.lock` installiert. `.env`
liefert `NFC_SERIAL_PORT` und `NFC_STATION_KEY`. Die zusätzliche `.station.env`
konfiguriert die UR-Anbindung; eine Vorlage liegt in `.station.env.example`.
Exportierte Umgebungsvariablen haben Vorrang. Geheimnisse gelangen nicht in die
Webseite. Dateien mit Zugangsdaten sind mit Modus `0600` anzulegen.

Auf mur620c lautet die relevante Konfiguration:

```dotenv
STATION_DASHBOARD_URL=http://10.145.8.144:8001
STATION_UR_HOST=ur10_r
STATION_RUNNING_VARIABLE=Laeuft
STATION_TIME_VARIABLE=Zeit
STATION_WINNER_VARIABLE=Gewinner
STATION_TIME_UNIT=seconds
STATION_WINNER_MAP='{"true":"win","false":"loss"}'
STATION_DIFFICULTY_SOURCE=override
```

Manueller Start:

```sh
cd ~/NDM_ws/src/match-hot-wire
./run-station.sh          # Port 8002; optional anderer Port als Argument
```

Der Benutzerdienst lässt sich entsprechend der [README](README.md) einrichten:

```sh
systemctl --user status ndm-station
systemctl --user restart ndm-station
journalctl --user -u ndm-station -f
```

Er startet automatisch mit der Benutzeranmeldung. Es ist kein systemweiter
Start vor der Anmeldung eingerichtet. Der Dienst verwendet `deploy/ndm-station.service`.
Das bisherige lokale Diagnose-Dashboard muss beendet sein, damit nur die Station
den USB-Port öffnet. Der zentrale Server bleibt davon unabhängig.

Der bestätigte erste Praxistest auf mur620c erfolgte aus dem ursprünglichen
Workspace `~/NDM_ws/src/ndm-nfc`. Der dort bereits laufende Dienst wird durch
einen Clone dieses Repositories nicht verändert. Für einen Umzug den bestehenden
Dienst stoppen, die privaten `.env`/`.station.env` und die `station.sqlite3`
konsistent übernehmen und dann die Dienstdatei aus diesem Repository installieren.
Anschließend das UR-Programm am Bedienpanel neu starten, damit seine Variablennamen
an die neue Verbindung übertragen werden.

`GET /api/state` auf Port 8002 liefert den Anzeigenzustand und die drei relevanten
UR-Werte zur Diagnose. `GET /health` prüft den Stationsprozess. Die Anzeige wird
automatisch aktualisiert und meldet einen Verbindungsverlust.

## Ergebnisse und Wiederanlauf

`station.sqlite3` enthält Versuche und eine dauerhafte Ausgangswarteschlange.
Vor dem ersten POST wird der vollständige Payload mit einer eindeutigen UUID
gespeichert. Netzwerkfehler werden mit genau derselben UUID und denselben Daten
wiederholt; so entstehen auch bei verlorenen Antworten keine Doppelwertungen.
Ausstehende Übertragungen werden nach einem Stationsneustart fortgesetzt.
Ein Neustart mitten im Spiel markiert den Versuch als unterbrochen.

Die möglichen Datenbankzustände sind `playing`, `finishing`, `pending`, `sent`,
`blocked` und `interrupted`. HTTP-Fehler wie falscher Schlüssel oder eine am Server
nicht vorhandene Schwierigkeit bleiben mit einer Fehlermeldung als `blocked`
gespeichert, statt das Ergebnis zu verwerfen. Nach Behebung lässt sich ein einzelner
geprüfter Datensatz gezielt wieder auf `pending` setzen; seine UUID und sein Payload
müssen dabei unverändert bleiben.

Die Zustandsdatenbank enthält Namen. Nicht als öffentliches Webverzeichnis
bereitstellen. Der systemd-Dienst erstellt neue Dateien mit `UMask=0077`.

## Prüfung

```sh
env -u PYTHONPATH uv run --locked pytest -q
```

Die Tests prüfen TCP-Framing, verschachtelte und über mehrere Pakete verteilte
UR-Werte, alle Override-Grenzen, stabile Ergebniswerte, Spielerbindung, Abbrüche,
Neustarts und einen vollständigen Ablauf über einen seriellen Pseudoterminal,
einen UR-Testserver und einen HTTP-Testserver mit verlorener Ergebnisantwort.
Diese Tests senden keine Resultate an das Veranstaltungs-Dashboard.

### Bestätigter Praxistest vom 18.09.2026

Auf `mur620c` wurde der vollständige Ablauf mit dem echten NFC-Reader und dem
Programm `NDW26_Draht.urp` auf `ur10_r` geprüft: Tag-Scan, Spielerbegrüßung,
Spielstart, laufende Anzeige, Spielende und automatische Übertragung.
Der Test mit **35 % Override (Stromsurfer)** endete nach **103,750 Sekunden** mit
`Gewinner=false`. Der Server bestätigte **Ergebnis-ID 5**; der passende Eintrag
war genau einmal auf dem betreffenden Spielerprofil sichtbar. Die lokale
Warteschlange war danach leer, und die Anzeige kehrte in den Wartezustand zurück.

In der ursprünglichen gemeinsamen Dashboard-/Stationsumgebung bestanden
**51 automatisierte Tests auf mur620c**, einschließlich
Sieg und Niederlage mit weiterlaufendem Zeit-Zähler sowie verlorener HTTP-Antwort.
Die Browseranzeige wurde in 15 Kombinationen aus Spielzustand und Bildschirmgröße
(1280×800, 800×480, 390×844) ohne JavaScript-Fehler oder horizontalen Überlauf geprüft.
