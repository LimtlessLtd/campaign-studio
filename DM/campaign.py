"""Where one campaign's files live, defined once.

A Campaign is a folder holding data, maps, uploads and backups; the folder above it holds the files the
site may show (FILE_ROOTS in campaign_core). Code finds the campaign it works on through active(), never
by computing paths from its own location, so a test or a relocated campaign changes one object.

The app folder is the default campaign. DM_HOME selects another one, and child processes inherit it.
"""

import contextlib
import os

INSTALL = os.path.dirname(os.path.abspath(__file__))  # the application's own code
ENVIRONMENT = 'DM_HOME'


class Campaign:
    """Paths of one campaign's documents and runtime folders."""

    def __init__(self, home):
        self.home = os.path.abspath(home)

    def __repr__(self):
        return f'Campaign({self.home!r})'

    def __eq__(self, other):
        return isinstance(other, Campaign) and self.home == other.home

    def __hash__(self):
        return hash(self.home)

    @classmethod
    def from_environment(cls):
        return cls(os.environ.get(ENVIRONMENT) or INSTALL)

    @property
    def files(self):
        """The folder holding the campaign's other files (the site shows some of them read-only)."""
        return os.path.dirname(self.home)

    @property
    def data(self):
        return os.path.join(self.home, 'data')

    @property
    def maps(self):
        return os.path.join(self.home, 'maps')

    @property
    def uploads(self):
        return os.path.join(self.home, 'uploads')

    @property
    def map_trash(self):
        """Deleted maps wait here until restored."""
        return os.path.join(self.home, 'trash', 'maps')

    @property
    def backups(self):
        """Verified copies made before a schema migration."""
        return os.path.join(self.home, 'backups')

    @property
    def history(self):
        return os.path.join(self.data, '.history')

    @property
    def commits(self):
        return os.path.join(self.data, '.commits')

    @property
    def jobs(self):
        return os.path.join(self.data, 'jobs')

    @property
    def settings(self):
        return os.path.join(self.data, 'settings.json')

    @property
    def map_index(self):
        return os.path.join(self.data, 'maps', 'index.json')

    def map_folder(self, slug):
        return os.path.join(self.maps, slug)

    def map_brief(self, slug):
        return os.path.join(self.data, 'mapbrief', slug + '.json')

    def relative(self, path):
        """A path as documents store it: relative to the campaign files folder, with / separators."""
        return os.path.relpath(path, self.files).replace(os.sep, '/')

    def child_env(self):
        """Environment entries that make a child process work on this campaign."""
        return {ENVIRONMENT: self.home}


_active = None


def active():
    """The campaign this process works on."""
    global _active
    if _active is None:
        _active = Campaign.from_environment()
    return _active


def activate(campaign):
    """Make campaign the one this process works on; returns the previous one."""
    global _active
    previous, _active = active(), campaign
    return previous


@contextlib.contextmanager
def using(campaign):
    """Work on campaign inside a with block, then return to the previous one."""
    previous = activate(campaign)
    try:
        yield campaign
    finally:
        activate(previous)
