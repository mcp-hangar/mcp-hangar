"""Process bootstrap: the composition root that wires the runtime.

Package marker. This directory shipped without one: ``runtime.py`` was tracked
and ``__init__.py`` was not, so the wheel carried the module inside an implicit
namespace package. Imports resolve either way, which is why nothing broke -- but
static tooling walking the package tree skips a directory with no marker, so the
module was invisible to import analysis.
"""
