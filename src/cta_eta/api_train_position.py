### VERY ROUGH CODE TO GET CTA TRAIN POSITIONS

# Get stations and their coordinates
import os
from typing import Final

import httpx
import stamina
from dotenv import load_dotenv

load_dotenv()

train_position_url = "http://lapi.transitchicago.com/api/1.0/ttpositions.aspx"

client = httpx.Client()

CTA_LINES: Final[list[str]] = ["red", "blue", "brn", "g", "org", "p", "pink", "y"]


@stamina.retry(on=httpx.HTTPStatusError, attempts=10)
def get_train_position(line: str) -> dict[str, str | float]:
    train_position = client.get(
        train_position_url,
        params={
            "key": os.getenv("CTA_API_KEY"),
            "rt": ",".join(CTA_LINES),
            "outputType": "JSON",
        },
    )
    train_position.raise_for_status()
    train_position = train_position.json()
    return train_position


# Example output:
# {
#     "ctatt": {
#         "tmst": "2026-01-14T20:34:15",
#         "errCd": "0",
#         "errNm": null,
#         "route": [
#             {
#                 "@name": "p",
#                 "train": [
#                     {
#                         "rn": "519",
#                         "destSt": "30176",
#                         "destNm": "Howard",
#                         "trDr": "5",
#                         "nextStaId": "40400",
#                         "nextStpId": "30079",
#                         "nextStaNm": "Noyes",
#                         "prdt": "2026-01-14T20:33:52",
#                         "arrT": "2026-01-14T20:34:52",
#                         "isApp": "1",
#                         "isDly": "0",
#                         "flags": null,
#                         "lat": "42.06106",
#                         "lon": "-87.68393",
#                         "heading": "150"
#                     },
#                     ...
#                 ]
#             },
#             ...
#         ]


# We'll need to poll this API every ~15-20 seconds to get the latest train positions.
# This also requires some advanced and robust data storage caching / saving / sending etc. logic
