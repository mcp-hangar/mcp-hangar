"""The principal a REST mutation is recorded against (#1720).

One fallback and one reader for every route that attributes a change: the
authenticated caller, or ``anonymous`` -- the id of the unauthenticated
principal -- when auth is off and there is nobody to name. Never ``system``,
which names Hangar itself.
"""

from starlette.requests import Request

from ...domain.value_objects.security import Principal


def request_actor(request: Request) -> str:
    """The id of the authenticated caller of *request*, or ``anonymous``.

    The auth context is read off ``scope["state"]`` in both shapes it is stored
    in -- a ``State`` by the outer enforcement wrapper, a plain dict by the
    router's own middleware.
    """
    state = request.scope.get("state")
    auth_context = state.get("auth") if isinstance(state, dict) else getattr(state, "auth", None)
    principal_id = getattr(getattr(auth_context, "principal", None), "id", None)
    return str(principal_id) if principal_id is not None else Principal.anonymous().id.value
