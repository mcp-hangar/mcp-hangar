**security:** filesystem discovery now refuses a definition file any local user
could have written. A discovered file names the command the gateway runs, yet
any file matching the pattern was registered, including a world-writable one,
one under a world-writable directory, and a symlink pointing out of the
discovery directory. Such a file is now skipped with one
`filesystem_discovery_file_refused` warning that names the reason. The mode is
read from the descriptor the file is parsed from. Symlinks that stay inside the
directory, the layout of a mounted Kubernetes ConfigMap, are still followed, and
group-writable files and directories are still accepted. On Windows the mode
checks are skipped
