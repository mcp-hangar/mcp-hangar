**core:** a configuration reload now puts a file's tool-access policies, group
policies included, withdrawals, pins and `header_exposure` blocks in force as
one set. It swapped them one after another, so a decision that read two of them
during a reload could combine the new value of one with the previous value of
another, a state neither file declares. The prompt and resource surfaces now
decide against the previous file's overlays or the new file's, never a mix.
