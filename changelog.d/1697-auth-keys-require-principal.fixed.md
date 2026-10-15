`GET /api/auth/keys` without `principal_id` answers 400 `principal_id is required` instead of 200 and an empty list, which read as "this principal has no keys".
