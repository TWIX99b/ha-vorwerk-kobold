# Vorwerk Kobold for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)

Control your **Vorwerk Kobold VR7** robot vacuum from Home Assistant – live, in
your own language, and with native **clean by area** support. Schedules and
robot settings of the MyKobold app are available too, and the data the app
syncs from a **VK7** stick vacuum.

> This is a community project. It is not affiliated with or endorsed by
> Vorwerk.

*Deutsch weiter unten.*

## Features

- **Live status** over the MyKobold cloud connection (push, no polling), with
  automatic reconnect and a periodic fallback refresh.
- **Vacuum entity**: start, pause, stop, return to base, locate, fan speed
  (auto / eco / turbo / pet, remembered across restarts).
- **Clean by area** (Home Assistant 2026.3+): map the rooms of your Kobold maps
  to Home Assistant areas once, then use `vacuum.clean_area` – no zone UUIDs.
- **Map images**: the floor plan of every named Kobold map and a thumbnail of
  the last cleaning run as image entities (refreshed after each run).
- **Sensors**: battery, status, error, cleaned area of the current / last run;
  dust bag and base station only with an RB7 service station; binary sensors
  for charging, docked and problem; a *Locate* button.
- **Schedules**: every schedule of the app as a switch (turn it on/off) with
  days, time, mode and map as attributes, plus a *Next scheduled cleaning*
  sensor.
- **Robot settings**: volume, sounds, voice assistant, voice language, object
  avoidance and low-passage detection – handy when little hands keep changing
  the language on the robot.
- **VK7 stick vacuum**: the data the app syncs from the VK7 (battery health,
  filter bag, maintenance trackers such as *clean brush* in %, total time and
  area). It only changes after the app has synced with the VK7 via Bluetooth.
  With Home Assistant Bluetooth (local adapter or an active proxy near the
  VK7) the integration also reads the VK7 directly whenever it is awake (in
  use or charging): **battery level**, filter bag and runtimes, plus an *In
  Bluetooth range* sensor. It only sends the same read requests as the app.
- **Proper login**: e-mail one-time code, token refresh, and a re-authentication
  flow instead of silently dying when the login expires.
- English and German translations, diagnostics download.

## Installation

1. HACS → three dots → *Custom repositories* → add
   `https://github.com/TWIX99b/ha-vorwerk-kobold` as *Integration*.
2. Install **Vorwerk Kobold** and restart Home Assistant.
3. *Settings → Devices & services → Add integration → Vorwerk Kobold*.
4. Enter the e-mail address of your MyKobold app, then the code you receive.

### Clean by area

Open the vacuum entity → *Clean by area* → *Configure areas* and assign each
Kobold room to a Home Assistant area. Afterwards:

```yaml
action: vacuum.clean_area
target:
  entity_id: vacuum.kobold
data:
  cleaning_area_id:
    - kitchen
    - living_room
```

Rooms of one call must be on the same Kobold map.

## Notes

- Only robots connected to the MyKobold cloud are supported (VR7; older
  VR200/VR300 use a different, retired cloud). The VK7 stick vacuum only talks
  Bluetooth to the app; Home Assistant sees what the app last uploaded.
- This uses the unofficial API of the MyKobold app and may break when Vorwerk
  changes it.
- The Bluetooth part for the VK7 is experimental. The VK7 only accepts
  connections while it is awake (in use, and for a while afterwards while it
  charges), so values update after use, not continuously. Close the MyKobold
  app on your phone when you want Home Assistant to read it – the VK7 talks to
  one device at a time.

---

## Deutsch

Steuert den **Vorwerk Kobold VR7** aus Home Assistant: Live-Status, Start,
Pause, Stopp, zur Basis, Roboter finden, Saugstufe, **Reinigung nach Bereichen**
(`vacuum.clean_area`), **Kartenbilder** (Grundriss und letzte Reinigung) sowie
Sensoren für Akku, Status, Fehler, Staubbeutel und gereinigte Fläche.
Dazu die **Zeitpläne** der App als Schalter, die **Einstellungen** (Lautstärke,
Audiosignale, Sprachassistent, Sprache, Objekte umfahren, niedrige Durchfahrten)
und die Daten des **VK7** (Akkuzustand, Filtertüte, Wartungszähler, Saugzeit),
soweit die App sie zuletzt synchronisiert hat.

Mit Home Assistant Bluetooth (z. B. ein aktiver Proxy in der Nähe) liest die
Integration den VK7 nach dem Saugen auch direkt aus: **Akkustand**,
Filtertüte und Laufzeiten.

Inoffizielles Community-Projekt, nicht von Vorwerk. Die Bluetooth-Funktionen
für den VK7 sind experimentell.

**Einrichtung:** Über HACS als benutzerdefiniertes Repository hinzufügen,
installieren, neu starten, dann *Einstellungen → Geräte & Dienste →
Integration hinzufügen → Vorwerk Kobold*. E-Mail der MyKobold-App eingeben und
den Code aus der E-Mail bestätigen. Läuft die Anmeldung ab, meldet Home
Assistant das unter *Reparaturen* – einfach neu anmelden.

## Credits

The cloud API was first figured out by
[jmzg93/koboldVR7Integration](https://github.com/jmzg93/koboldVR7Integration).
See `NOTICE`. Not affiliated with Vorwerk.
