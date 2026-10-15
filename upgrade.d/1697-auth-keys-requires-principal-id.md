### `GET /api/auth/keys` requires `principal_id`

A request without `principal_id` (or with a blank one) now gets HTTP 400 instead of 200 with an empty list. Pass the principal whose keys you want.
