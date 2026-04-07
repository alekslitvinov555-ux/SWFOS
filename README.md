# smart-wagon-flow

MVP scaffolding for a **Smart Wagon Flow Optimization System** for Ukrainian Railway.

This prototype uses:
- **NetworkX** for graph modeling and shortest-path routing.
- **Pandas** for future data operations/analytics extensions.
- **Folium** for map visualization.
- **Streamlit** for a lightweight interactive UI.

## What it does

1. Loads rail stations and track data from JSON files.
2. Builds a directed graph where:
   - Nodes = stations (capacity, current load, coordinates)
   - Edges = rail connections (base time, max capacity, current flow)
3. Runs Dijkstra with a **custom congestion-aware cost function** that strongly penalizes stations above 90% utilization.
4. Displays the chosen route on an interactive map.

## Project structure

- `data/stations.json` — mock station capacity/load and coordinates.
- `data/edges.json` — mock rail links and capacities.
- `data/railways_southern_ukraine.geojson` — real OSM railway vector network (generated).
- `src/graph_builder.py` — builds `networkx.DiGraph` from JSON.
- `src/routing.py` — congestion-aware routing logic.
- `app.py` — Streamlit entrypoint and Folium map rendering.
- `fetch_rail_network_geojson.py` — downloads Southern Ukraine railway GeoJSON from Overpass.

## Quick start

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Run the app:

```bash
streamlit run app.py
```

3. Open the local Streamlit URL shown in the terminal.

## Run tests

```bash
python -m unittest discover -s tests -v
```

## Live vs Fallback demo mode (track geometry)

The script `fetch_osm_tracks.py` enriches key railway corridors in `data/edges.json`
with dense waypoint geometry for realistic map rendering.

### Modes

- `--mode auto` (recommended for development):
   - Tries Overpass API first.
   - Falls back to offline dense geometry if API fails/timeouts.
- `--mode live`:
   - Uses only Overpass API.
   - Fails if API is unavailable.
- `--mode fallback` (recommended for hackathon demo):
   - No network dependency.
   - Uses stable prebuilt dense geometries.

### PowerShell commands

```powershell
Set-Location 'd:\УЗ_Хакатон\smart-wagon-flow'
d:/УЗ_Хакатон/.venv/Scripts/python.exe fetch_osm_tracks.py --mode auto
```

```powershell
Set-Location 'd:\УЗ_Хакатон\smart-wagon-flow'
d:/УЗ_Хакатон/.venv/Scripts/python.exe fetch_osm_tracks.py --mode live
```

```powershell
Set-Location 'd:\УЗ_Хакатон\smart-wagon-flow'
d:/УЗ_Хакатон/.venv/Scripts/python.exe fetch_osm_tracks.py --mode fallback
```

### Team recommendation for presentation day

1. Run `--mode fallback` before the demo to lock stable high-fidelity geometry.
2. Start Streamlit app.
3. If internet is reliable, optionally switch to `--mode auto` before final run.

## Professional GIS mode (Approach A: GeoJSON Highlighting)

We use a real railway vector layer from OSM and highlight route segments on top of that geometry.

### Install

```bash
pip install -r requirements.txt
```

### 1) Download real railway network as GeoJSON

```powershell
Set-Location 'd:\УЗ_Хакатон\smart-wagon-flow'
d:/УЗ_Хакатон/.venv/Scripts/python.exe fetch_rail_network_geojson.py
```

Optional modes:

```powershell
Set-Location 'd:\УЗ_Хакатон\smart-wagon-flow'
d:/УЗ_Хакатон/.venv/Scripts/python.exe fetch_rail_network_geojson.py --mode auto
```

```powershell
Set-Location 'd:\УЗ_Хакатон\smart-wagon-flow'
d:/УЗ_Хакатон/.venv/Scripts/python.exe fetch_rail_network_geojson.py --mode live
```

```powershell
Set-Location 'd:\УЗ_Хакатон\smart-wagon-flow'
d:/УЗ_Хакатон/.venv/Scripts/python.exe fetch_rail_network_geojson.py --mode fallback
```

### 2) Run dashboard

```powershell
Set-Location 'd:\УЗ_Хакатон\smart-wagon-flow'
d:/УЗ_Хакатон/.venv/Scripts/python.exe -m streamlit run app.py
```

## Fast demo mode (optimized tracks)

To avoid lag and map re-rendering during pan/zoom, preprocess rail geometry once and run the app from a lightweight file.

### Build optimized tracks file

```powershell
Set-Location 'd:\УЗ_Хакатон\smart-wagon-flow'
d:/УЗ_Хакатон/.venv/Scripts/python.exe build_optimized_tracks.py
```

This creates `data/optimized_tracks.json` with:
- simplified geometry,
- largest connected rail component,
- precomputed station-to-station track paths.

`app.py` reads this file directly during demo runtime (no heavy GIS processing on the fly).

### How it works

- The app loads `data/railways_southern_ukraine.geojson` and draws the full track network as LineStrings.
- For each segment of the optimized station route, it maps endpoints to nearest vector-rail nodes.
- It highlights the shortest path on that geometric network, producing Waze-like curved route rendering.

## Notes

- The routing cost is **not ML-based**; it is deterministic and operations-research oriented.
- You can tune penalties in `src/routing.py` to simulate different dispatch policies.
