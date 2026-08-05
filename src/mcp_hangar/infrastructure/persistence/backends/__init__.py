"""Storage backends. One is selected whole; they are never mixed.

Each module here owns its driver and its SQL. `sqlite` knows `sqlite3`,
`postgresql` knows `psycopg2`, and no adapter outside its own backend carries a
dialect branch -- which is what makes adding a third backend a package rather
than a patch.
"""
