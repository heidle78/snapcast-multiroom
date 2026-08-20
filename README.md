# Snapcast Multiroom Controller

Ein Docker-Container, der - vom Grundgedanken her wie
[Multi-SendSpin-Player-Container](https://github.com/chrisuthe/Multi-SendSpin-Player-Container) -
mehrere virtuelle Multiroom-Player auf einem Host verwaltet, jeweils an einen
eigenen Audio-Ausgang (USB-DAC, HDMI, Onboard-Sound, ...) gebunden. Statt
Sendspin läuft hier pro Ausgang ein eigener **`snapclient`**-Prozess
(Snapcast-Protokoll), gesteuert über eine kleine Web-UI/REST-API.

Music Assistant bringt einen eingebauten Snapcast-Provider mit - dieser
Container erzeugt einfach für jeden physischen Ausgang genau den Client, den
dieser Provider erwartet.

## Warum Snapcast statt Sendspin?

- Music Assistant hat einen nativen, ausgereiften Snapcast-Provider.
- Die Puffer-/Latenzzeit beim Start bzw. nach Pause/Skip ist **direkt
  konfigurierbar** (`buffer_time`, `fragments`, `--latency`) statt eines
  starren, undokumentierten SDK-Werts.
- Deutlich stabiler bei WLAN-/Netzwerk-Schwankungen (Community-Erfahrung).
- Passt exakt auf "ein Host, mehrere USB-DACs" - der klassische
  Snapcast-Anwendungsfall.

## Quick Start

1. `docker-compose.yml` anpassen: `SNAPSERVER_HOST` auf die IP deines
   Music-Assistant-Hosts setzen (dort muss der Snapcast-Provider aktiviert
   sein: **Settings → Player Providers → Snapcast**).
2. Container bauen und starten:

   ```bash
   docker compose up -d --build
   ```

3. Web-UI öffnen: `http://<docker-host>:8098`
4. Optional: über **Einstellungen → Setup-Assistent** einmalig Backend
   (ALSA/PulseAudio) und Snapserver hinterlegen.
5. Über **"+ Player hinzufügen"** einen Player pro Audio-Ausgang anlegen.
   Das Gerätefeld ist ein Dropdown mit den automatisch erkannten Ausgängen
   (ALSA: `aplay -l`; PulseAudio: alle geladenen Sinks inkl. Custom Sinks).
6. Player erscheinen innerhalb weniger Sekunden in Music Assistant unter
   dem eingestellten Namen.

## Web-UI

Neben der Player-Verwaltung (Bootstrap-Oberfläche mit Live-Status, Start/
Stop/Neustart, Bearbeiten, Löschen) bietet **Einstellungen** im Header:

- **System-Einstellungen** — Backend (`alsa`/`pulse`), Standard-Snapserver,
  Standardwerte für Buffer/Fragments/Latenz neuer Player.
- **PulseAudio Custom Sinks** — nur bei Backend `pulse`: mehrere Ausgänge zu
  einem *Combine*-Sink zusammenfassen (z. B. zwei Räume synchron bespielen)
  oder einen *Remap*-Sink anlegen (z. B. nur den linken Kanal eines Stereo-
  Ausgangs verwenden).
- **Setup-Assistent** — geführte Ersteinrichtung (Backend, Snapserver).
- **Diagnose exportieren** — lädt ein Textbündel aus Einstellungen, Playern,
  erkannten Geräten/Sinks und den letzten Logzeilen herunter, praktisch für
  Fehlermeldungen/Support.

Ein eigener **Logs**-Tab zeigt die zusammengeführten Logs aller Player mit
Filter nach Player, Volltextsuche und Auto-Aktualisierung.

## Umgebungsvariablen

| Variable          | Default       | Beschreibung                                             |
|-------------------|---------------|-----------------------------------------------------------|
| `SNAPSERVER_HOST` | -             | Standard-Snapserver-Host (i. d. R. der Music-Assistant-Host) |
| `SNAPSERVER_PORT` | `1704`        | Standard-Snapserver-Port                                   |
| `WEB_PORT`        | `8098`        | Port der Web-UI/API                                        |
| `LOG_LEVEL`       | `info`        | `debug`, `info`, `warning`, `error`                         |
| `CONFIG_PATH`     | `/app/config` | Ablageort der Player-Konfiguration (YAML)                  |
| `LOG_PATH`        | `/app/logs`   | Ablageort der Player-Logs (eine Datei je Player)            |

Der Snapserver-Host/-Port lässt sich zusätzlich pro Player überschreiben
(z. B. wenn ein Ausgang an einen anderen Snapserver soll).

## Backend: ALSA vs. PulseAudio

Standardmäßig läuft jeder Player direkt gegen ein ALSA-Gerät
(`--soundcard hw:X,Y`) - einfach, ein Ausgang je Player. Wird in den
System-Einstellungen auf **PulseAudio** umgestellt, startet der Container
einen lokalen PulseAudio-Daemon (`--system`-Modus, da der Container ohne
eigenen `USER` als root läuft - das vermeidet Ärger mit unterschiedlichen
Audio-Gruppen-IDs zwischen Host und Container bei `/dev/snd`) und legt für
jedes erkannte ALSA-Gerät automatisch einen passenden Hardware-Sink an.
Erst dieses Backend schaltet **Custom Sinks** frei: mehrere Ausgänge zu
einem Sink kombinieren (`module-combine-sink`) oder Kanäle remappen
(`module-remap-sink`, z. B. einen Stereo-Ausgang in zwei Mono-Player
aufteilen). Player zeigen dann mit `--player pulse:...` auf den gewählten
Sink statt auf ein rohes ALSA-Gerät.

## Pro-Player-Einstellungen

| Feld              | Bedeutung                                                                                   |
|-------------------|-----------------------------------------------------------------------------------------------|
| `device`          | ALSA-Device, z. B. `plughw:1,0` (empfohlen, macht Format-/Rate-Konvertierung) oder `hw:1,0`   |
| `buffer_time_ms`  | ALSA-Ausgabepuffer in ms (snapclient-Default: 80). Kleiner = weniger Verzögerung, größer = robuster gegen Aussetzer |
| `fragments`       | Anzahl ALSA-Puffer-Fragmente (Default 4, min. 2)                                              |
| `latency_ms`      | Fester Ausgleich in ms für Verstärker-/Lautsprecher-Eigenlatenz (`--latency`)                  |
| `sampleformat`    | Erzwungenes Sample-Format, z. B. `48000:16:2`                                                 |
| `extra_args`      | Beliebige zusätzliche `snapclient`-CLI-Argumente                                              |
| `enabled`         | Player beim Containerstart automatisch starten                                                |

Alles ist über die Web-UI editierbar; Änderungen starten den betroffenen
Player automatisch neu und werden in `config/players.yaml` persistiert.

## REST API (Auszug)

```
GET    /api/health
GET    /api/devices
GET    /api/players
POST   /api/players
PUT    /api/players/{name}
DELETE /api/players/{name}
POST   /api/players/{name}/start
POST   /api/players/{name}/stop
POST   /api/players/{name}/restart
GET    /api/players/{name}/logs?lines=100
GET    /api/logs?player=&search=&lines=500
GET    /api/settings
PUT    /api/settings
GET    /api/sinks
POST   /api/sinks
DELETE /api/sinks/{name}
GET    /api/diagnostics
```

## Wie ein Player im Hintergrund gestartet wird

Für jeden Player wird sinngemäß Folgendes ausgeführt:

```bash
snapclient -h <host> -p <port> \
  --hostID <name> \
  --soundcard <device> \
  --player alsa:buffer_time=<buffer_time_ms>,fragments=<fragments> \
  --latency <latency_ms> \
  --logsink stdout
```

Der Prozess wird überwacht: stürzt er ab, startet der Controller ihn mit
steigendem Backoff (2s, 4s, 8s, ... max. 30s) automatisch neu, bis er manuell
gestoppt wird. Die letzten ~300 Log-Zeilen pro Player sind live über die
Web-UI einsehbar, zusätzlich wird alles nach `logs/<name>.log` geschrieben.

## Troubleshooting

**Keine Geräte im Dropdown / "No such device":**
Prüfen, ob `/dev/snd` in den Container gemountet ist und ob
`docker exec snapcast-multiroom aplay -l` innerhalb des Containers
überhaupt Geräte listet. Auf manchen Docker-Hosts braucht es zusätzlich
`--group-add audio` bzw. die passenden Berechtigungen auf `/dev/snd/*`.

**Player verbindet nicht zu Music Assistant:**
- `SNAPSERVER_HOST`/`SNAPSERVER_PORT` prüfen (Standard-Port 1704).
- Ports 1704 und 1705 auf dem Music-Assistant-Host müssen erreichbar sein.
- Logs des Players in der Web-UI ansehen ("Logs"-Button auf der Player-Karte).

**Player-Antwortzeit bei Pause/Play tunen:**
`buffer_time_ms` und `fragments` in den Player-Einstellungen reduzieren
senkt die Verzögerung, erhöht aber die Anfälligkeit für Aussetzer bei
Netzwerk-Jitter. In der Praxis meist zwischen 40-150 ms sinnvoll.

**Ausgangsverzögerung durch Verstärker/Lautsprecher:**
Über `latency_ms` (bzw. direkt in Music Assistants Snapcast-Player-
Einstellungen) kompensieren.

## Unterschiede zum Original-Sendspin-Projekt

Übernommen wurde die Bootstrap-Oberfläche samt Setup-Assistent,
System-Einstellungen, Logs-Ansicht und Diagnose-Export. Bewusst nicht
übernommen wurde die 12V-Trigger-/Relais-Steuerung, da sie nichts mit dem
Audio-Pfad zu tun hat und für Snapcast-Setups selten gebraucht wird - bei
Bedarf lässt sie sich ergänzen. Der Code ist in fünf Python-Dateien
gehalten: `main.py` (FastAPI/REST), `manager.py` (snapclient-Prozess-
Supervision), `devices.py` (ALSA-Geräteerkennung), `pulse.py`
(PulseAudio-Orchestrierung für das optionale `pulse`-Backend) und
`config_store.py` (YAML-Persistenz).
