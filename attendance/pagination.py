"""Working out which slice of a list to show.

Extracted when the admin page became the second thing that needed paging. The arithmetic
is small but easy to get subtly wrong -- an off-by-one in the offset silently skips or
repeats a row, and a page count that rounds the wrong way hides the last few records.
Doing it once means getting it right once.

Framework-free on purpose: the route reads the query string, this decides what that
means, and it can be tested without an application at all.
"""

DEFAULT_SIZE = 50


class Page:
    """Which rows to fetch, and what to tell the reader about where they are."""

    def __init__(self, number, size, total):
        self.size = size
        self.total = total

        # at least one page, so an empty list still reads "page 1 of 1" rather than
        # "page 1 of 0", which looks like a bug
        self.count = max(1, -(-total // size))  # ceiling division without importing math

        # clamped rather than rejected: ?page=999 on a list that has shrunk is a stale
        # bookmark, and page 0 or -4 is a mangled url. Neither is worth an error page.
        self.number = min(max(number or 1, 1), self.count)

        self.offset = (self.number - 1) * size

    @property
    def has_previous(self):
        return self.number > 1

    @property
    def has_next(self):
        return self.number < self.count

    @property
    def previous(self):
        return self.number - 1

    @property
    def next(self):
        return self.number + 1

    @property
    def needed(self):
        """Whether to draw the controls at all. One page of results needs no navigation."""
        return self.count > 1


def paginate(number, total, size=DEFAULT_SIZE):
    return Page(number, size, total)
