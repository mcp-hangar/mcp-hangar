**core:** the shared redactor now recognises credentials in a URL's userinfo
(`postgres://user:pw@host/db`, `https://x-access-token:ghp_...@github.com/...`),
replacing them while leaving the scheme and host readable. The approval
sanitizer documented connection strings as covered from the day it was written
and no pattern matched them, so its own cited example was written verbatim into
the approval record. Applies everywhere the redactor runs, including the log
pipeline and stderr capture
