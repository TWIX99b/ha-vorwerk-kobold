# Vorwerk Kobold for Home Assistant

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)

Control your **Vorwerk Kobold VR7** robot vacuum from Home Assistant – live, in
your own language, and with native **clean by area** support.

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
  Bluetooth to the app and is not reachable through the cloud.
- This uses the unofficial API of the MyKobold app and may break when Vorwerk
  changes it.

---

## Deutsch

Steuert den **Vorwerk Kobold VR7** aus Home Assistant: Live-Status, Start,
Pause, Stopp, zur Basis, Roboter finden, Saugstufe, **Reinigung nach Bereichen**
(`vacuum.clean_area`), **Kartenbilder** (Grundriss und letzte Reinigung) sowie
Sensoren für Akku, Status, Fehler, Staubbeutel und gereinigte Fläche.

**Einrichtung:** Über HACS als benutzerdefiniertes Repository hinzufügen,
installieren, neu starten, dann *Einstellungen → Geräte & Dienste →
Integration hinzufügen → Vorwerk Kobold*. E-Mail der MyKobold-App eingeben und
den Code aus der E-Mail bestätigen. Läuft die Anmeldung ab, meldet Home
Assistant das unter *Reparaturen* – einfach neu anmelden.

## Credits

The cloud API was first figured out by
[jmzg93/koboldVR7Integration](https://github.com/jmzg93/koboldVR7Integration).
See `NOTICE`. Not affiliated with Vorwerk.
