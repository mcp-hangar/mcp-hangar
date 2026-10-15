`max_consecutive_failures` is checked against its documented range of 1 to 100 when the config loads. 0 and 500 used to boot; they now refuse the config, as does a value that is not an integer.
