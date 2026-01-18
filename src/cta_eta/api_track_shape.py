"""CTA Track Shape API Example Code."""

url = "https://data.cityofchicago.org/api/v3/views/xbyr-jnvx/query.json"

# NOTE: use HTTP POST to get the data.
# Use `X-App-Token` / `X-App-Secret` header for authentication.
# with CHIDATA_APP_TOK / CHIDATA_APP_SECRET environment variables.

# Example output saved in data/cta_track_shape.json
# Relatively long cache is acceptable since track shape doesn't change often.

"""
Example output:
{":id":"row-jfz4-3j8m~bhsb",":version":"rv-ucfc-iy3g~j5vu",":created_at":"2024-07-23T22:38:55.007Z",":updated_at":"2024-07-23T22:38:55.007Z","the_geom":{"type":"MultiLineString","coordinates":[[[-87.62820914531717,41.87690834080102],[-87.62756718362677,41.87693211074777],[-87.62649605340081,41.87695930020092],[-87.6263491331091,41.87696047741115],[-87.62625048735104,41.87694557717528],[-87.62612052039513,41.87688045834185],[-87.62605121676445,41.87680618468352],[-87.62603003565505,41.87670362155761]]]},"lines":"Brown, Orange, Pink, Purple (Express)","description":"Tower 12 to Library","type":"Elevated or At Grade","legend":"ML","shape_len":"647.793224715"},
"""

# Each track segment is a MultiLineString between two stations (or some other waypoint) with a list of coordinates.

# We want to save the list of lines that the track segment is part of.

# We also want to save the two endpoints of the track segment.
# These USUALLY match the station names, but not always.
# For example, Tower 12 is the southeastern corner of the downtown Loop track,
# which connects to Harold Washington Library, Roosevelt/Wabash, and Adams/Wabash.
# We see the pattern is generally "X to Y" in the description field.
# While the true graph is bidirectional directed, I think the data names here are saved as if it was an undirected graph.

# Finally, we also want to save the length of the track segment
# and the type of track (Elevated or At Grade, Subway).
