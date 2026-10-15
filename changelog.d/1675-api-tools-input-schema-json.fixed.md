The `input_schema` of each tool in `GET /api/tools` is a JSON object. It was `str()` of a dict, a Python repr that no JSON parser reads; a tool with no schema reads `{}`.
