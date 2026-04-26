"""Source registry: maps a source type name to the scraper class that handles it.

To add a new source type:
  1. Write a new scraper file in src/scrapers/{name}.py exposing a class that
     subclasses BaseScraper.
  2. Add it to the SCRAPERS dict below.
  3. Add entries in config/sources.yaml.
"""
from __future__ import annotations

from .greenhouse import GreenhouseScraper
from .lever import LeverScraper
from .ashby import AshbyScraper
from .smartrecruiters import SmartRecruitersScraper
from .workable import WorkableScraper
from .recruitee import RecruiteeScraper
from .remotive import RemotiveScraper
from .aijobs import AIJobsScraper
from .working_nomads import WorkingNomadsScraper
from .hackernews import HackerNewsScraper
from .ycombinator import YCWorkAtStartupScraper
from .jobspresso import JobspressoScraper

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
