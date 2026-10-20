"""EDINET API v2 wrapper (edinet-wrapper 0.1.0).

Vendored from https://github.com/pekumirui/edinet-api (private) src/edinet_wrapper @ a3a2f01.
Do not edit these files (callers live in app/edinet/). The only change from upstream is
`from edinet_wrapper.x` -> `from .x`. Requires Python 3.11+ (StrEnum, typing.Self);
import it only from EDINET code paths so the app itself still starts on 3.10.
"""

from ._version import __version__
from .async_client import AsyncEdinetClient
from .client import EdinetClient
from .codelist import (
    download_code_aggregation_list,
    download_edinet_code_list,
    download_fund_code_list,
    resolve_edinet_code,
)
from .enums import (
    DisclosureStatus,
    DocInfoEditStatus,
    DocType,
    DocumentFileType,
    LegalStatus,
    ListType,
    Ordinance,
    WithdrawalStatus,
)
from .errors import (
    EdinetAPIError,
    EdinetAuthError,
    EdinetBadRequestError,
    EdinetConfigError,
    EdinetError,
    EdinetFileNotAvailableError,
    EdinetMaintenanceError,
    EdinetNotFoundError,
    EdinetRateLimitError,
    EdinetServerError,
    EdinetTransportError,
    EdinetUnexpectedResponseError,
)
from .files import DownloadedFile, UnsafeZipError
from .models import (
    CodeAggregationEntry,
    DocumentEntry,
    DocumentList,
    DocumentListMetadata,
    EdinetCodeEntry,
    FundCodeEntry,
)
from .utils import (
    date_range,
    is_previous_days_finalized,
    normalize_sec_code,
    today_jst,
)

__all__ = [
    "AsyncEdinetClient",
    "CodeAggregationEntry",
    "DisclosureStatus",
    "DocInfoEditStatus",
    "DocType",
    "DocumentEntry",
    "DocumentFileType",
    "DocumentList",
    "DocumentListMetadata",
    "DownloadedFile",
    "EdinetAPIError",
    "EdinetAuthError",
    "EdinetBadRequestError",
    "EdinetClient",
    "EdinetCodeEntry",
    "EdinetConfigError",
    "EdinetError",
    "EdinetFileNotAvailableError",
    "EdinetMaintenanceError",
    "EdinetNotFoundError",
    "EdinetRateLimitError",
    "EdinetServerError",
    "EdinetTransportError",
    "EdinetUnexpectedResponseError",
    "FundCodeEntry",
    "LegalStatus",
    "ListType",
    "Ordinance",
    "UnsafeZipError",
    "WithdrawalStatus",
    "__version__",
    "date_range",
    "download_code_aggregation_list",
    "download_edinet_code_list",
    "download_fund_code_list",
    "is_previous_days_finalized",
    "normalize_sec_code",
    "resolve_edinet_code",
    "today_jst",
]
