# Chicago Train ETA Model

Machine Learning Model + Data Collection for predicting estimated time of arrival (ETA) for trains from the Chicago Transit Authority (CTA).

## Monitoring CLI

The monitoring CLI is installed as `cta-monitor` via the project script entry point.

- **From the project directory:** `uv run cta-monitor [--base-dir /override/path] status|errors|gaps|metrics|compaction`
- **From any directory:** `uv run --project /path/to/cta-eta cta-monitor status`

Paths (`.daemon_state`, `data`, etc.) are resolved relative to the base directory. By default this comes from `[paths].project_root` in `config.toml` (falling back to the project root). On a deployed host (e.g. Oracle Cloud with app at `/opt/cta-eta`), set in `config.toml`:

```toml
[paths]
project_root = "/opt/cta-eta"
```

Then you can run with:

```bash
uv run --project /opt/cta-eta cta-monitor status
```

## Project Plan

### 1. Data Collection.

Data Collection is purely API based.

#### API Resources
Very important to read the CTA API documentation: <https://www.transitchicago.com/developers/ttdocs/#_Toc296199912> to see nuances, particularly Appendix D.

#### Tracks
We'll use the Chicago Open Data Portal to get track segment data. This is in the form of MultiLineString for each track segment between stations. We also want to save the track type, and parse the endpoints.

See `api_cta_track_shape.py` in src/cta_eta for more notes.

#### Stations
We'll use the Chicago Open Data Portal to get station latitude / longitude. See `api_stations_weather.py` in src/cta_eta.

#### Trains
Use the [CTA Train Tracker API](https://www.transitchicago.com/developers/ttdocs/), specifically the `ttpositions` endpoint, to get train data.

Example query: `python f"http://lapi.transitchicago.com/api/1.0/ttpositions.aspx?key=os.getenv("CTA_API_KEY")&rt=red&outputType=JSON"`

Example output: 
```json
{"ctatt":{"tmst":"2026-01-14T20:34:15","errCd":"0","errNm":null,"route":[{"@name":"p","train":[{"rn":"519","destSt":"30176","destNm":"Howard","trDr":"5","nextStaId":"40400","nextStpId":"30079","nextStaNm":"Noyes","prdt":"2026-01-14T20:33:52","arrT":"2026-01-14T20:34:52","isApp":"1","isDly":"0","flags":null,"lat":"42.06106","lon":"-87.68393","heading":"150"},{"rn":"520","destSt":"30203","destNm":"Linden","trDr":"1","nextStaId":"41050","nextStpId":"30203","nextStaNm":"Linden","prdt":"2026-01-14T20:33:52","arrT":"2026-01-14T20:34:52","isApp":"1","isDly":"0","flags":null,"lat":"42.06762","lon":"-87.68762","heading":"333"},{"rn":"521","destSt":"30203","destNm":"Linden","trDr":"1","nextStaId":"40050","nextStpId":"30010","nextStaNm":"Davis","prdt":"2026-01-14T20:33:16","arrT":"2026-01-14T20:34:16","isApp":"1","isDly":"0","flags":null,"lat":"42.04165","lon":"-87.6816","heading":"346"}]}]}}
```

Note that there are eight train lines (rt): red, blue, brn, g, org, p, pink, y.

The CTA has a rate limit of 50,000 requests per day and the API updates roughly every 15-20 seconds, but the data has about a 10 second lag. 50k requests is sufficient for pinging the server every 15 seconds with the "all train positions" query.

Other important facts about the data are...
1. I don't trust the `prdt` (prediction time) and `arrT` (arrival time) fields to be accurate, particularly during delays. These fields come from the CTA, which gets ETA prediction times from the position of the train within its signal blocks. Crucially, the `prdt` and `arrT` fields do NOT appear to update unless the train moves to the next signal block. This means that we might be dealing with old ETA predictions from the CTA, particularly if the train is stuck in the same signal block and not moving. The true timestamp for when the API call occurred is the `tmst` field, which is probably worth saving. I believe the CTA `isDly` (is delayed flag) field is raised to 1 if there are no updates to the train `prdt` or `arrT` field from the train being stopped, but this has a ~1-3min lag. 
2. Because the `arrT` arrival time ETA is not true, we can't use this as our "source-of-truth" for labels. We'll have to generate this ourselves based on some other factor, probably train position.

Architecture Considerations:
- We'll need to have a robust API client (httpx? with retry logic and error handling? maybe stuff like tenacity for sending requests?)
- We'll need a robust way of handling and storing data. This requires both compute and storage. Compute needs to be always on with reasonably high uptime so that we can continue pinging the CTA. Storage needs to be both on cloud compute (the computer) AND probably some object storage S3 like thing as backup AND have some way to transmit files to local computer occasionally for double backup.
    - Currently thinking code is going to be run on a virtual private server (VPS) like [Oracle Cloud Infrasturcture](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm#Details_of_the_Always_Free_Compute_instance__a1_flex), or more hopefully whatever free AWS EC2 / Lightsail / S3 storage or GCP Compute Engine / Cloud Storage compute I can get. I might be willing to pay for the more industry standard option, because Oracle is hmm even if it's free.
    - File transmission to local is probably okay as weekly / daily parquet files. Not 100% sure how to transmit that yet, might be email or API/SDK, or worst case manual download?

#### Weather
We probably are going to use two providers -- `open-meteo.com` and the National Weather Service API (and maybe `openweathermap.org` if needed).

`open-meteo.com` has a daily API limit of 10,000 calls per day. 
Example call:
`https://open-meteo.com/en/docs?latitude=41.721183248936335&longitude=-87.62437673162748&timezone=America%2FChicago&forecast_days=1&hourly=&current=temperature_2m,relative_humidity_2m,apparent_temperature,rain,showers,snowfall,weather_code,cloud_cover,surface_pressure,wind_speed_10m,wind_direction_10m,wind_gusts_10m&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch`

Example output:
```json
{"latitude":41.715942,"longitude":-87.63699,"generationtime_ms":0.15807151794433594,"utc_offset_seconds":-21600,"timezone":"America/Chicago","timezone_abbreviation":"GMT-6","elevation":180.0,"current_units":{"time":"iso8601","interval":"seconds","temperature_2m":"°F","relative_humidity_2m":"%","apparent_temperature":"°F","rain":"inch","showers":"inch","snowfall":"inch","weather_code":"wmo code","cloud_cover":"%","surface_pressure":"hPa","wind_speed_10m":"mp/h","wind_direction_10m":"°","wind_gusts_10m":"mp/h"},"current":{"time":"2026-01-14T21:00","interval":900,"temperature_2m":17.5,"relative_humidity_2m":64,"apparent_temperature":5.4,"rain":0.000,"showers":0.000,"snowfall":0.000,"weather_code":3,"cloud_cover":100,"surface_pressure":993.4,"wind_speed_10m":13.6,"wind_direction_10m":319,"wind_gusts_10m":30.4}}
```

open-meteo is awesome and doesn't even need an API key. Notice the `interval` field (and current units) is 900 or 15min, which should be the update frequency for weather. Note that we can't do this in 10k daily requests, since (24\*60/15 \* 146 stations) = 14k calls. Instead, we're going to pull the weather for each station once to create a mapping between CTA stations and weather stations, and then only call the weather stations. Initial testing on 2026-01-14 finds there are only `39` unique weather stations (24\*60/15 \* 39) = 3744, so we get in under the limit, even after the 1.2x API call size penalty brings us up to ~4499.

I think we'll use open-meteo for hourly weather forecast data which can't be found in the National Weather Service API.

openweathermap.org could also be used to get future forecasts (1 hour forecast is probably sufficient). It gives 1,000 calls per day however.
Hoewver, I'm thinking we just do this directly with the National Weather Service API.
- Get the location
- Get the hourly forecast
- Use open-meteo or openweathermap for any other fields which are missing.

For more information, see `api_stations_weather.py`.

### 2. Data Processing
Once we've collected the data, we need to do the following operations:

#### Data Merge
Merge weather and station information together. Use the mapping from train stations to weather stations and vice versa. Reset the mapping every 24hr after some internal cache timer status has run out (e.g., check last mapping modification date stored in a metadata file in a file cache via a dedicated file cache class).

Flatten train location data and save over time. Use some robust system that a Senior Data Engineer would approve of. Rolling buffers and parquet files and save to object storage? Something else?

#### Connecting Train Latitude Longitude to Track and Stations
Because I do not think that `arrT` is a clean enough field, I think we need to generate labels based on train position.

Thus, I need some kind robust geosptial utility methods which quickly handles geospatial coordinate comparisons.

1. Determine accurate distance between two coordinates. Feel free to use GeoPy or something else to take into account WGS-84 shape, even if the correction is minor. This is going to be useful to determine if the train is "close enough" to the station that it is considered truly "arrived".
2. Snap and project train coordinates onto the nearest track which also is on the same line as the train (i.e., don't want to snap to tracks from different lines, as unlikely as that might be).
3. Convert points along train tracks into track distance travelled. This must take into consideration the actual track geometry as defined in the MultiLineString. Note that we might need to look at multiple segments of track, e.g., when calculating distance travelled between updates if updates occurred before and after a station.
4. Convert train latitude/longitude data over time into train speed measures over the past minute, three minutes, and five minutes. This must use the distance along tracks metric.
5. Convert train position into % of track segment travelled metric, using current position distance versus track segment length. Note that you will have to infer the starting point to use based on the train's ultimate destination (i.e., the train's `destSt` or `destNm`, and a mapping of which ultimate destinations correspond to which track orientations, which is a bit hard), OR use the `heading` from the train + the location to infer the train's travelling direction on the track segment.

#### ETA Labels
Create some kind of system which gets the true ETA. 

- As mentioned earlier, I don't trust the CTA's clained ETA flags, particularly if there are delays. We'll keep track of the `arrT` time minus `prdT` as one option. This option cannot be the true arrival time IF the `prdT` is old (1min+) or we have a delay flag `isDly`, because this indicates the train hasn't moved outside the current signal block and thus predictions are old. 
- The way I was hoping to do this is use the distance between the train location and the station location, and flag as `arrived` if this is sufficiently small. However, I'm not really seeing the train latitude/longitude updating when the train is in the station, only afterwards. Perhaps the station track doesn't have a block detection signal.
- A conservative way to do ETA is that clearly we have arrived if the next station `nextStaId` has changed. Equivalents to this are if the train run `rn` has disappeared by reaching the terminal station on the route, or a new train run `rn` appears in the data. However, I'm pretty sure this is a lagging metric.
- Finally, stations at the beginning of the line have a train schedule, and I don't think have a true ETA available (other than schedule).

#### Other Computed Features
We should use our track distance utilities to also add the track segment distance completed, % of track segment distance completed, lagging velocities (1m, 3m, 5m), station dwell times (`arrT`? vs leaving for the next station), or any other useful features.

### 3. Basic Model
DO NOT IMPLEMENT THIS YET, WIP.

We're going to need to create a basic model for calibration. This is probably just LightGBM, and then maybe RNN.

### 4. Advanced Model
DO NOT IMPLEMENT THIS YET, WIP.

This is going to be a spatiotemporal graph neural network using PyTorch Geometric.