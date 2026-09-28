# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.4.1] - 2026-09-28

### Fixed
- **Disruption Station Alias Isolation**: Fixed an issue where passing train origins and destinations in `next_trains` were appended to `origin_names` and `dest_names` in `disruption.py`, turning passing service termini into station identity aliases. This caused remote incidents (such as engineering work at Dartford or distant termini like Charing Cross) to match passing query stations (e.g. Blackheath) and spuriously trigger `engineering_work` service status or display unrelated station announcements.
- **Genuine Matching Preserved**: Station aliases are now strictly confined to queried station names, CRS codes, and supported canonical station fallbacks (including `BKH` -> `Blackheath` and configured sensor names), preserving legitimate route notices, destination alerts, and genuine station closure detection.

## [1.4.0] - 2026-09-27

### Added
- Knowledgebase observability: exposed snapshot counts, active incident counts, and station mention counts.
- Weekend disruption matching refinements and extended closure validity boundaries.
- Station closure matching in Knowledgebase incident descriptions and alternative travel text.
- Station sensor name token normalization for suffixes (`_station`, ` Station`) and underscore separation.
