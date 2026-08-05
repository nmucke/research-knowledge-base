"""Domain-specific exceptions exposed as readable CLI failures."""


class ResearchKBError(Exception):
    """Base class for expected application errors."""


class ZoteroUnavailableError(ResearchKBError):
    """Raised when the Zotero API cannot be reached."""


class ZoteroAuthorizationError(ResearchKBError):
    """Raised when a Zotero write is not authorized."""


class ZoteroConflictError(ResearchKBError):
    """Raised when a Zotero write loses a version race."""


class BetterBibTeXUnavailableError(ResearchKBError):
    """Raised when Better BibTeX JSON-RPC cannot be reached."""


class CitationKeyMissingError(ResearchKBError):
    """Raised when an item has no Better BibTeX citation key."""


class PDFNotFoundError(ResearchKBError):
    """Raised when an item has no resolvable PDF attachment."""


class PDFExtractionError(ResearchKBError):
    """Raised when usable text cannot be extracted from a PDF."""


class MarkdownParseError(ResearchKBError):
    """Raised when a paper note cannot be parsed safely."""


class ManagedBlockError(ResearchKBError):
    """Raised when managed-block markers are invalid."""


class ValidationError(ResearchKBError):
    """Raised when vault or note validation fails."""
