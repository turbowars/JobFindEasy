"""Source registry: maps a source type name to the scraper class that handles it.

To add a new source type:
  1. Write a new scraper file in src/scrapers/{name}.py exposing a class that
     subclasses BaseScraper.
  2. Add it to the SCRAPERS dict below.
  3. Add entries in config/sources.yaml.
"""

from __future__ import annotations

from .aijobs import AIJobsScraper
from .ashby import AshbyScraper
from .greenhouse import GreenhouseScraper
from .hackernews import HackerNewsScraper
from .jobspresso import JobspressoScraper
from .lever import LeverScraper
from .recruitee import RecruiteeScraper
from .remotive import RemotiveScraper
from .smartrecruiters import SmartRecruitersScraper
from .workable import WorkableScraper
from .working_nomads import WorkingNomadsScraper
from .ycombinator import YCWorkAtStartupScraper

# Slug-based scrapers: take a list of company slugs
ATS_SCRAPERS = {
    "greenhouse": GreenhouseScraper,
    "lever": LeverScraper,
    "ashby": AshbyScraper,
    "smartrecruiters": SmartRecruitersScraper,
    "workable": WorkableScraper,
    "recruitee": RecruiteeScraper,
}

# Endpoint-based scrapers: take an options dict from the YAML
ENDPOINT_SCRAPERS = {
    "remotive": RemotiveScraper,
    "aijobs": AIJobsScraper,
    "working_nomads": WorkingNomadsScraper,
    "hackernews": HackerNewsScraper,
    "ycombinator": YCWorkAtStartupScraper,
    "jobspresso": JobspressoScraper,
}

ALL_SCRAPERS = {**ATS_SCRAPERS, **ENDPOINT_SCRAPERS}
