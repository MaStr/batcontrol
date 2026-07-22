# Evaluation: Solar-Einspeisegrenze (Solarspitzengesetz) im Peak-Shaving

Status: **Evaluations- und Simulationsphase** — noch nicht in `logic/next.py` integriert.
Simulationsskript: [`scripts/simulate_solar_limit_day.py`](https://github.com/MaStr/batcontrol/blob/main/scripts/simulate_solar_limit_day.py)

## Hintergrund: die 60-Prozent-Regel

Das Solarspitzengesetz (in Kraft seit 25.02.2025) begrenzt für ungesteuerte PV-Anlagen
(ohne iMSys + Steuerbox) die Wirkleistungseinspeisung am Netzanschlusspunkt auf **60 % der
installierten Leistung** (§ 9 Abs. 2 Nr. 3 EEG). Der Wechselrichter setzt die Grenze hart
durch: Erzeugung oberhalb der Grenze wird **abgeregelt und ist verloren**, außer sie wird
selbst verbraucht oder in den Akku geladen.

Für eine 10-kWp-Anlage heißt das: maximal 6 000 W Einspeisung. An einem klaren Sommertag
mit ~8,9 kW Spitzenleistung liegen mehrere Stunden über der Grenze; ohne Gegenmaßnahme
gehen an so einem Tag ca. **7,5 kWh verloren** (siehe Referenzszenario unten).

Wichtig: Es handelt sich um eine **Leistungsgrenze, keine Mengengrenze**. Die Verluste
konzentrieren sich auf die Mittagsspitze — genau das Zeitfenster, in dem der Akku
bei naivem Verhalten längst voll ist.

## Kernbefund: das heutige Peak-Shaving kann Abregelung verursachen

Das bestehende Peak-Shaving (Modi `time`/`price`) setzt einen **Cap** auf die
PV-Ladeleistung (`limit_battery_charge_rate`). Liegt der PV-Überschuss über der
Einspeisegrenze, blockiert dieser Cap genau die Energie, die sonst in den Akku müsste —
die Differenz wird abgeregelt. Im Referenzszenario regelt das reine Zeit-Shaving
1,8 kWh ab, die mit der neuen Regel vollständig gerettet werden.

Umgekehrt hilft das Zeit-Shaving bereits teilweise (76 % Rückgewinnung vs. 0 % Baseline),
weil es Kapazität in den Nachmittag verschiebt — aber unkoordiniert und ohne Garantie.

## Vorgeschlagener Algorithmus: Regel "solar_cap"

Die neue Regel arbeitet mit den vorhandenen Forecast-Arrays (Wh pro Intervall,
Index 0 = jetzt) und kennt zwei Fälle. Pro Slot `k` (bis zum Ende des Produktionsfensters):

```
surplus_wh[k]    = max(0, production[k] - consumption[k])
feed_allow_wh[k] = feed_in_limit_w * slot_h[k]
clip_wh[k]       = min(surplus_wh[k], max(0, surplus_wh[k] - feed_allow_wh[k]) * headroom)
```

**Fall A — vor dem Kappungsfenster: Reservierungs-Cap.**
Freie Kapazität minus prognostizierte Kappungsenergie wird gleichmäßig über die Slots
bis Fensterbeginn verteilt. Ist die Reserve größer als die freie Kapazität, wird das
PV-Laden komplett geblockt (Cap 0). Damit verdrängt einspeisbare Energie nicht 1:1 die
Kappungsenergie im Akku.

**Fall B — im Kappungsfenster: Floor + kapazitätsschonender Cap.**

```
floor_w = clip_raw_wh[0] / slot_h[0]        # Pflicht-Laderate, ohne headroom
cap_w   = -1                                 wenn Gesamt-Surplus <= freie Kapazität
        = floor_w + extra_wh / restliche_h   sonst (extra = freie Kap. - restliche Kappung)
```

Bei Knappheit (`extra = 0`) gilt `cap == floor`: Der Akku nimmt **nur** Kappungsenergie
auf, alles unterhalb der Grenze wird eingespeist. Eine Priorisierung innerhalb des
Fensters ist unnötig — jede absorbierte Kappungs-Wh ist gleichwertig; schädlich ist
allein das Füllen der Kapazität mit einspeisbarer Energie.

Der Floor wird aus der **Roh-Kappung ohne headroom** berechnet: Es wird nie Energie
zwangsgeladen, die legal eingespeist werden könnte.

## Konfigurationsdesign: Schalter pro Regel

Mit drei Regelsorten (Zielzeit, Preis, Solar) wird der bisherige `mode`-String
(`time`/`price`/`combined`) unübersichtlich. Beschlossenes Design: **ein expliziter
Schalter pro Regel**, `mode` wird deprecated und beim Einlesen auf die Schalter gemappt
(`time` → `time_active`, `price` → `price_active`, `combined` → beide):

```yaml
peak_shaving:
  enabled: false                 # Master-Schalter (wie bisher, inkl. evcc-Override)
  time_active: true              # Zielzeit-Regel (counter-linearer Ramp)
  price_active: false            # Preis-Regel (Reserve fuer Billigfenster)
  solar_cap_active: false        # NEU: Kappungs-Absorption (Einspeisegrenze)
  allow_full_battery_after: 14   # Parameter der Zielzeit-Regel
  price_limit: 0.05              # Parameter der Preis-Regel
  feed_in_limit_w: 0             # Parameter der Solar-Regel: Einspeisegrenze in W.
                                 # 0 = Neutralstellung (Regel wirkungslos, auch wenn
                                 # solar_cap_active true ist). Formel: 0.6 * kWp * 1000
  feed_in_limit_headroom: 1.0    # Sicherheitsfaktor >= 1.0 auf die prognostizierte
                                 # Kappungsenergie (nur Reservierung, nie Floor)
```

`feed_in_limit_w` ist bewusst ein **absoluter Wattwert**: Die installierte Leistung (kWp)
steht heute nur bei fcsolar-`pvinstallations` in der Config (bei Solcast gar nicht), und
die Grenze gilt am Netzanschlusspunkt der Gesamtanlage. `0` ist die Neutralstellung —
zusätzlich zum Schalter, damit eine unkonfigurierte Grenze nie versehentlich als
"0 W Einspeisung erlaubt" interpretiert wird.

### Prioritäten zwischen den Regelsorten

Dokumentierte, feste Rangfolge (keine Konfiguration nötig):

1. **`enabled` (Master)** aus → keine Regel wirkt (inkl. evcc-Laufzeit-Override).
2. **Force-Charge aus dem Netz (MODE -1)** überstimmt jedes Peak-Shaving (wie heute).
3. **Alle aktiven Cap-Regeln** (Zielzeit-Ramp, Preis-Reserve, Solar-Reservierung)
   liefern je ein Limit; das **strengste gewinnt** (`min`, wie heute bei `combined`).
4. **Der Solar-Floor überstimmt jeden Cap**: `final = max(floor, min(caps))`.
   Begründung: Caps optimieren Ökonomie (Ladung verschieben), der Floor verhindert
   **physischen Verlust** (Abregelung). Ein Cap unterhalb des Floors würde Energie
   vernichten. Deshalb gilt der Floor auch **nach** `allow_full_battery_after` und
   auch bei hohem SoC (`always_allow_discharge`-Region) — das Kappungsfenster dauert
   physikalisch länger als die Zielstunde. Konsequenz: Die Solar-Reservierung kann den
   Akku erst nach der Zielstunde voll werden lassen; verlorene Energie wiegt schwerer
   als ein später voller Akku.
5. **Statische Inverter-Klemmen** zuletzt (`max_pv_charge_rate` als Obergrenze,
   500-W-Minimum via `enforce_min_pv_charge_rate`). Achtung: ein konfiguriertes
   `max_pv_charge_rate` unterhalb des Floors macht Abregelung physisch unvermeidbar
   → Startup-Warnung vorgesehen.

Sentinel-Semantik bleibt: `-1` = kein Limit, `0` = Laden blocken. `-1` erfüllt jeden
Floor automatisch, weil der Inverter Überschuss dann ohnehin greedy in den Akku lädt —
es ist **kein neuer Inverter-Modus** nötig, der Floor ist die Garantie
`angewandter Cap >= floor`.

## Simulationsergebnisse

Alle Zahlen aus `scripts/simulate_solar_limit_day.py` (Referenz: 10 kWp Süd, klarer
Sommertag, Peak 8,9 kW, Grenze 6 000 W, 10 kWh Akku, 400 W Grundlast, Start-SoC 15 %,
Stundenraster). "Rückgewinnung" = Anteil der ohne Akku abgeregelten Energie, der
gerettet wird.

### Szenario 1 — Referenztag

| Trace                            | Eingespeist | Abgeregelt | Rückgewinnung |
|----------------------------------|------------:|-----------:|--------------:|
| Baseline (alle Regeln aus)       | 40,50 kWh   | 7,50 kWh   | 0 %           |
| Nur `time_active` (heute)        | 46,20 kWh   | 1,80 kWh   | 76,0 %        |
| Nur `solar_cap_active`           | 48,00 kWh   | 0,00 kWh   | **100 %**     |
| `time_active + solar_cap_active` | 48,00 kWh   | 0,00 kWh   | **100 %**     |

End-SoC ist in allen Traces identisch (83,3 %) — die Regel verschenkt nichts, sie
verschiebt nur, **womit** der Akku gefüllt wird. Im Slot-Detail sichtbar: Vor dem
Fenster begrenzt der Reservierungs-Cap auf 625 W; ab 11:00 hebt der Floor die Laderate
exakt auf die Kappungsleistung (1 200 → 2 500 → 2 400 → 1 400 W), die Einspeisung
steht dabei konstant auf 6 000 W. In der Kombination überstimmt der Floor den
Zeit-Ramp-Cap genau dann, wenn dieser Abregelung verursachen würde.

### Szenario 2 — Ost/West-Profil (Peak 5,6 kW < Grenze)

Keine Kappung erwartet; die Regel bleibt vollständig inert — Trace bitidentisch zur
Baseline (Regressionsprüfung bestanden, keine False Positives).

### Szenario 3 — Kleiner Akku (5 kWh, Knappheit)

Freie Kapazität bei Fensterbeginn 5,00 kWh, Kappungspotenzial 7,50 kWh:

| Trace                  | Abgeregelt | Rückgewinnung |
|------------------------|-----------:|--------------:|
| Baseline               | 7,50 kWh   | 0 %           |
| Nur `solar_cap_active` | 2,50 kWh   | 66,7 %        |

Zurückgewonnen: **5,00 kWh = exakt die freie Kapazität bei Fensterbeginn** — das
theoretische Maximum. Die Reservierung blockt morgens das PV-Laden komplett (Cap 0,
Einspeisung läuft unterhalb der Grenze weiter), im Fenster gilt `cap == floor`.

### Szenario 4 — Prognosefehler (Forecast = 85 % der Realität)

| Trace                       | Abgeregelt | Rückgewinnung |
|-----------------------------|-----------:|--------------:|
| Baseline                    | 7,50 kWh   | 0 %           |
| solar, headroom 1.0         | 4,96 kWh   | 33,8 %        |
| solar, headroom 1.2         | 4,46 kWh   | 40,6 %        |
| solar, headroom 1.5         | 4,23 kWh   | 43,5 %        |
| solar, perfekter Forecast   | 0,00 kWh   | 100 %         |

Erkenntnisse: (a) Der Algorithmus ist deutlich forecast-sensitiv — eine
15-%-Unterschätzung der Produktion unterschätzt die Kappung überproportional (Kappung
ist die "Spitze" der Kurve). (b) `headroom` auf die Kappungsenergie verbessert die
Reservierung nur moderat (+7 Punkte bei 1.2), weil im Fenster auch der **Floor** aus
dem zu niedrigen Forecast berechnet wird. Der daraus abgeleitete Maßnahmenplan wird in
Szenario 4b entwickelt und quantifiziert.

### Szenario 4b — Schwerer Prognosefehler (Ist = 125 % der Prognose)

Die Prognose sieht nur **1,36 kWh** Kappungspotenzial statt real 7,50 kWh und erkennt
ganze Kappungs-Slots (11:00, 14:00) **gar nicht** als solche — ein Multiplikator auf die
prognostizierte Kappungsenergie kann das strukturell nicht reparieren. Da batcontrol
**keine Live-Messung der aktuellen Produktion** hat, stehen nur prognosebasierte
Gegenmaßnahmen zur Verfügung; zwei wurden implementiert und verglichen:

- **headroom auf den Überschuss** (`headroom_on='surplus'`): Der Faktor wird vor der
  Kappungsberechnung auf den prognostizierten Überschuss angewandt. Das rekonstruiert
  eine unterschätzte Produktionskurve und findet auch übersehene Kappungs-Slots —
  repariert die **Reservierung** vor dem Fenster.
- **headroom-Floor** (`floor_source='headroom'`): Der Floor im Fenster wird aus der
  headroom-korrigierten statt der Roh-Kappung berechnet. Bei greedy ladenden Invertern
  ist der Floor ohnehin nur eine **Erlaubnis** (der angewandte Cap wird angehoben, der
  Inverter lädt `min(Ist-Überschuss, Cap)`) — es wird nie Ladung erzwungen, die es
  physisch nicht gibt. Repariert die **Absorption im Fenster**.

Ergebnis unter beiden Bedingungen (Ist = 125 % der Prognose bzw. Prognose korrekt):

| Einstellung                              | Rückgew. bei +25 % Fehler | Rückgew. bei korrekter Prognose |
|------------------------------------------|--------------------------:|--------------------------------:|
| headroom 1.25 auf Kappung (Roh-Floor)    | 38,7 %                    | —                                |
| headroom 1.25 auf Überschuss (Roh-Floor) | 31,8 %                    | —                                |
| Überschuss 1.1 + headroom-Floor          | 44,9 %                    | 94,5 % (Verlust 0,41 kWh)        |
| Überschuss 1.25 + headroom-Floor         | **94,7 %**                | 62,7 % (Verlust 2,80 kWh)        |
| Neutral (headroom 1.0)                   | 31,8–38,7 %               | **100 %**                        |

Zentrale Erkenntnisse:

1. Beide Maßnahmen sind **nur zusammen** wirksam: Ohne headroom-Floor ist die perfekte
   Reservierung wertlos (der forecast-basierte Cap blockt das Laden, während real
   gekappt wird — deshalb ist "Überschuss allein" sogar leicht schlechter als "Kappung
   allein"); ohne Überschuss-headroom ist der Akku bei Fensterbeginn schon vorgefüllt.
2. **Ohne Live-Messung ist der headroom ein echter Trade-off**: Er muss ungefähr zum
   typischen Prognosefehler passen. Ein zu hoher Wert (1.25 bei korrekter Prognose)
   lädt im Fenster einspeisbare Energie und verdrängt an kapazitätsknappen Tagen
   Kappungsenergie 1:1 (2,8 kWh Verlust). Ein zu niedriger Wert lässt Kappung liegen.
3. **1.1 ist der robuste Kompromiss**: kostet bei korrekter Prognose nur 0,41 kWh und
   verbessert den Fehlerfall bereits deutlich.

**Plan für Prognosefehler (Festlegung für die Integration, rein prognosebasiert):**

1. **`feed_in_limit_headroom` wirkt auf den prognostizierten Überschuss** und der
   **Floor wird aus der headroom-korrigierten Kappung** berechnet (eine gemeinsame
   Stellschraube, kein zweiter Config-Key). Default `1.0` (neutral, verlustfrei bei
   korrekter Prognose); dokumentierte Empfehlung `1.1`, bei bekannt schlechter
   Prognosequelle bis `1.25`.
2. Nebenwirkungen dokumentieren: headroom > 1 kann an Tagen knapp unterhalb der Grenze
   eine unnötige Reservierung auslösen (Akku später voll, kein Energieverlust) und an
   kapazitätsknappen Kappungstagen bei korrekter Prognose einen kleinen Teil der
   Kappung verdrängen (quantifiziert oben).
3. Das 15-Minuten-Raster (`time_resolution_minutes: 15`) reduziert den systematischen
   Anteil des Fehlers zusätzlich (Szenario 6).
4. **Zukunftsoption** (nicht v1, erfordert neuen Datenpfad): eine Live-Messung der
   aktuellen Produktion/Einspeisung würde den Floor prognoseunabhängig machen und den
   Trade-off auflösen — batcontrol erfasst diese Werte derzeit nicht.

### Szenario 5 — Mittags-Verbrauchsspitze (2,4 kW, 12–14 Uhr)

Eigenverbrauch senkt das Kappungspotenzial auf 3,50 kWh; Kombination
`time + solar_cap` gewinnt 100 % zurück (Baseline 0 %, nur-Zeit 60 %).

### Szenario 6 — 15-Minuten-Raster

Konsistenzprüfung am interpolierten Referenztag: 99,1 % Rückgewinnung (Restverlust
0,07 kWh durch Interpolationskanten an Slot-Grenzen). Das 15-Minuten-Raster reduziert
zusätzlich den systematischen Fehler "Stundenmittel unterschätzt Momentankappung".

## Bewertung

Der Algorithmus erfüllt die Anforderungen:

1. **Er rettet die "40 %"**: 100 % Rückgewinnung bei korrektem Forecast, exakt das
   physikalische Maximum bei knappem Akku.
2. **Er repariert einen Defekt**: Ohne den Floor verursacht das bestehende Peak-Shaving
   an Kappungstagen selbst Verluste (1,8 kWh am Referenztag).
3. **Er ist minimal-invasiv**: kein neuer Inverter-Modus, keine neue Datenquelle,
   gleiche Sentinel-Semantik, additiv als Post-Processing-Schritt.
4. **Er ist neutral, wenn er nichts zu tun hat** (Ost/West-Szenario) und per
   `feed_in_limit_w: 0` bzw. `solar_cap_active: false` vollständig abschaltbar.

Bekannte Grenzen: Forecast-Sensitivität (Szenarien 4/4b) — ohne Live-Messung der
aktuellen Produktion (derzeit nicht Bestandteil von batcontrol) bleibt der headroom ein
Trade-off, dessen Wert zum typischen Prognosefehler passen muss (Empfehlung 1.1);
Stundenmittel vs. Momentanleistung (ein Slot mit Mittel knapp unter der Grenze kann
real kurzzeitig kappen — durch headroom teilweise abgedeckt).

## Integrations-Roadmap (Folgeschritt)

1. `logic/logic_interface.py`: `PeakShavingConfig` um `time_active`, `price_active`,
   `solar_cap_active`, `feed_in_limit_w` (Default 0 = neutral), `feed_in_limit_headroom`
   (Default 1.0) erweitern; `mode` deprecaten und in `from_config()` auf die Schalter
   mappen (Warnung loggen); Validierung analog `price_limit`.
2. Neues `logic/solar_limit.py`: `compute_solar_limit()` und `merge_limits()` aus dem
   Simulationsskript unverändert übernehmen (pure Funktionen, Muster
   `grid_charge_target.py`).
3. `logic/next.py`: eigener Post-Processing-Schritt `_apply_solar_limit()` **nach**
   `_apply_peak_shaving()` mit eigener (kleinerer) Skip-Liste: läuft auch bei hohem SoC
   und nach `allow_full_battery_after`; skippt bei Force-Charge und (v1) bei
   `allow_discharge == False` (dort lädt der Inverter Überschuss ohnehin ungebremst).
   Merge nach der Prioritätsregel oben; `enforce_min_pv_charge_rate` einmalig auf den
   final gemergten Wert. Helper `_remaining_interval_hours()` extrahieren
   (anteiliger Slot 0, vgl. Grid-Recharge-Block). `feed_in_limit_headroom` wirkt auf
   den prognostizierten Überschuss und der Floor nutzt die headroom-korrigierte
   Kappung (`headroom_on='surplus'`, `floor_source='headroom'` im Simulationsskript;
   Trade-off siehe Szenario 4b).
4. `core.py`: Startup-Warnung wenn `feed_in_limit_w > 0` und `max_pv_charge_rate > 0`.
5. Tests: `tests/batcontrol/logic/test_solar_limit.py` (pure Funktionen) + Integrationsfälle
   in `test_peak_shaving.py` (Floor überstimmt Cap inkl. Cap 0, Reservierung, Knappheit
   `cap == floor`, Neutralstellung = bitidentisches Verhalten, Sentinels, Slot-0-Anteiligkeit,
   15-min, mode-Deprecation-Mapping).
6. `config/batcontrol_config_dummy.yaml` + `docs/features/peak-shaving.md` +
   HA-Add-on-Spiegelung (`MaStr/batcontrol_ha_addon`).
7. Offen für die Integration: Live-Messung als Floor-Quelle für Slot 0 (siehe Szenario 4);
   aktives Entladen vor dem Fenster (v1: nein, nur passive Reservierung); MQTT-Topic
   `predicted_clip_wh` (read-only, optional).
