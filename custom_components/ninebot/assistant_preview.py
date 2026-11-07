"""Local-only detail gaps, using the archive's existing bounded timeline view."""

from dataclasses import dataclass

from .archive_browse import RecordedMonth, RecordedPage
from .preservation import DetailScope, scope_fingerprint


@dataclass(frozen=True)
class DetailPreview:
    """Known local coverage, not an upstream total or payload integrity audit."""

    revision: int
    months: tuple[RecordedMonth, ...]
    skipped_conflicting_times: int
    saved_count: int
    missing_count: int
    unmapped_count: int
    scope: DetailScope | None

    @property
    def known_count(self) -> int:
        return self.saved_count + self.missing_count + self.unmapped_count

    @property
    def selected_count(self) -> int:
        return len(self.scope.rides) if self.scope else 0

    @property
    def has_more(self) -> bool:
        return self.missing_count > self.selected_count


def detail_preview(page: RecordedPage) -> DetailPreview:
    """Freeze at most twenty missing mappings, after excluding indexed details."""
    saved = missing = unmapped = 0
    candidates: list[tuple[str, str]] = []
    for row in page.records:
        ride = row.ride
        if ride.ride_id in page.saved_detail_ids:
            saved += 1
        elif (
            not ride.ride_id
            or not ride.detail_id
            or {"conflicting_ride_ids", "conflicting_detail_ids"} & set(ride.issues)
        ):
            unmapped += 1
        else:
            missing += 1
            if len(candidates) < 20:
                candidates.append((ride.ride_id, scope_fingerprint(ride)))
    # Release the full bounded scan; retain only month coverage and selection metadata.
    return DetailPreview(
        page.revision,
        page.months,
        page.skipped_conflicting_times,
        saved,
        missing,
        unmapped,
        DetailScope(tuple(candidates)) if candidates else None,
    )
