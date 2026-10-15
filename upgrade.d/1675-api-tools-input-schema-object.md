### `GET /api/tools` returns `input_schema` as an object

`input_schema` was a string holding a Python repr (`"{'type': 'object'}"`). It is now the JSON object itself, and `{}` when a tool declares none. A client that parsed the string must read the field directly.
