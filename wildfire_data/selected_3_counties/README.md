Selected counties:
- Santa Barbara County: southern/coastal county with relatively frequent wildfire activity in this dataset.
- Shasta County: northern California county requested as a comparison case.
- Sacramento County: third county selected for clean cross-table coverage.

Files:
- `fire_data_3counties.csv`
- `weather_data_3counties.csv`
- `ndvi_data_3counties.csv`
- `CA_Counties_Location_3counties.csv`

Coverage summary:
- Santa Barbara County: fire 118, weather 150, ndvi 156
- Shasta County: fire 84, weather 150, ndvi 156
- Sacramento County: fire 2, weather 150, ndvi 156

Notes:
- In `fire_data.csv`, county names use the `... County` form.
- In `ndvi_data.csv` and `CA_Counties_Location.csv`, county names drop the word `County`.
- Sacramento has full weather/NDVI/location coverage, but only 2 fire records in the fire table. If you want a stronger low-fire comparison county, consider replacing it after checking other candidates.
