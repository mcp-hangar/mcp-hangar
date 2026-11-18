**core:** the time a call is held for approval no longer counts against its
call budget. The hold ran inside the budget, so a front-door flat call (30 s),
a `hangar_call` with a short `timeout` and a facade `invoke` (`timeout_s`, 30 s
by default) were refused `CancellationError` when a human approved after the
budget had passed, although the approval was reported as successful and the
call never ran. A call approved inside its approval window now runs exactly
once, with the budget it had when the hold began. A call nobody approves is
still refused when its window ends and its approval is recorded `expired`. A
call that is not held keeps the budget it had
