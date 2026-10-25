**security:** an API key created with an `expires_at` that has no UTC offset now
works until it expires. It was accepted and then every authentication with it
answered 500, because the store compared the offset-naive expiry with an aware
clock. Such a value is now read as UTC, when the key is created and when a key
stored that way before is read back, and an unparseable `expires_at` answers 422
instead of 500
