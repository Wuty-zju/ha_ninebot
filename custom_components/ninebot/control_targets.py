"""Maintainer-confirmed command targets; motion/P policy belongs to the vehicle."""

# Only these maintainer-confirmed operations have an observable lock target.
# None is reserved for non-lock controls such as bell.
LOCK_TARGETS: dict[str, tuple[str, bool]] = {
    "engine/start": ("vehicle_lock", False),
    "engine/stop": ("vehicle_lock", True),
    "buck": ("seat_lock", False),
}
