"""Small reviewed account display metadata, never a copy of whoami."""

from dataclasses import dataclass, field

from .parsing import raw_scalar, text

ACCOUNT_METADATA = "account_display"
AUTOMATIC_TITLE = "automatic_title"


def display_text(value: object) -> str | None:
    scalar = raw_scalar(value)
    return text(scalar) if isinstance(scalar, str) and len(scalar) <= 64 else None


@dataclass(frozen=True)
class AccountDisplay:
    username: str | None = field(default=None, repr=False)
    region: str | None = field(default=None, repr=False)

    @classmethod
    def parse(cls, raw: object) -> "AccountDisplay":
        if not isinstance(raw, dict):
            return cls()
        return cls(display_text(raw.get("username")), display_text(raw.get("region")))

    def as_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in (("username", self.username), ("region", self.region))
            if value is not None
        }

    def title(self, account: str) -> str:
        title = f"{self.username}：{account}" if self.username else account
        return f"{title}[{self.region}]" if self.region else title


def account_update(
    data: dict,
    account: str,
    display: AccountDisplay,
    title: str | None = None,
    *,
    force: bool = False,
) -> tuple[dict[str, str], str, str]:
    """Retain last known metadata and protect a user-customized entry title."""
    metadata = AccountDisplay.parse(data.get(ACCOUNT_METADATA)).as_dict() | display.as_dict()
    automatic = AccountDisplay.parse(metadata).title(account)
    selected = (
        title
        if not force and title is not None and title not in ("Ninebot", data.get(AUTOMATIC_TITLE))
        else automatic
    )
    return metadata, automatic, selected
