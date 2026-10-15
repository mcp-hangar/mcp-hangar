### `IRoleStore` requires `list_principals()`

A custom role store (a class implementing `IRoleStore` outside this package) must now implement `list_principals() -> list[dict]`, returning one `{"principal_id": ..., "roles": [...]}` per principal that holds a role. The four stores that ship with Hangar already do. Without it the store cannot be instantiated.
