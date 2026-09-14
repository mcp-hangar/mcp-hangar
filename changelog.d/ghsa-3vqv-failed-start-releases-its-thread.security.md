**security:** a failed start of a stdio upstream now returns promptly and
stops the process it launched (GHSA-3vqv-89q2-8m26). When the start failed
while the upstream was still running, building the error's diagnostics read the
process's stderr until it closed, which happened only when the process exited.
The starting thread waited there, the server stayed `initializing`, and callers
waiting on the start timed out.

The start error now carries only the stderr the stdio client captured, and a
failed start closes the client it launched, so the upstream no longer runs on
after it. When an upstream's stdout closes, Hangar collects its stderr for at
most one second rather than until the pipe closes, and fails the calls that
were waiting on it.
