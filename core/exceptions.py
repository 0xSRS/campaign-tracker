"""Custom exception hierarchy.

Catching CampaignTrackerError catches anything this app raises on purpose.
Catching a narrower subclass lets each layer handle only what it understands.
"""


class CampaignTrackerError(Exception):
    """Base class for all application-specific errors."""


# --- Configuration -----------------------------------------------------------
class ConfigurationError(CampaignTrackerError):
    """Settings or environment values are missing or invalid."""


class CampaignConfigError(ConfigurationError):
    """The campaign course file is missing, malformed or invalid."""


# --- Data import -------------------------------------------------------------
class CsvImportError(CampaignTrackerError):
    """Base class for CSV import problems."""


class CsvFileError(CsvImportError):
    """The file is missing, unreadable or not valid UTF-8 text."""


class CsvFormatError(CsvImportError):
    """The file is readable but empty, or its header/columns are wrong."""


class InvalidProfileURLError(CsvImportError):
    """A submitted URL is not a valid Google Skills public profile URL."""
    
# --- Database ----------------------------------------------------------------
class DatabaseError(CampaignTrackerError):
    """A database operation failed."""

# --- Export ------------------------------------------------------------------
class ExportError(CampaignTrackerError):
    """A report could not be prepared or written."""

# --- Scraping ----------------------------------------------------------------
class ScraperError(CampaignTrackerError):
    """Base class for profile fetching and parsing problems."""


class ProfileFetchError(ScraperError):
    """Network error, timeout or unexpected server response.

    Maps to the FAILED status, never to 'zero certifications'.
    """


class ProfileParseError(ScraperError):
    """The page was fetched but its structure was not what we expected.

    Maps to FAILED or NEEDS_REVIEW depending on the situation.
    """
    
class InvalidRecordError(DatabaseError):
    """Raised when data handed to a repository would violate storage invariants."""